import hashlib
import logging
import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connections, transaction
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.subscriptions.models import Plan, Subscription
from apps.tenants.audit import log_audit_event
from apps.tenants.models import AuditLog, Invitation, Membership, Tenant
from apps.tenants.permissions import CanManageMembers, IsOrganizationAdmin
from apps.tenants.provisioning import provision_tenant_database
from apps.tenants.serializers import (
    AuditLogSerializer,
    InvitationAcceptSerializer,
    InvitationCreateSerializer,
    InvitationSerializer,
    MemberRoleUpdateSerializer,
    MembershipSerializer,
    OrganizationCreateSerializer,
    OrganizationMembershipSerializer,
)

logger = logging.getLogger(__name__)


class ThrottledTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "token_obtain"


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "token_refresh"


@require_GET
def health(request):
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as exc:
        logger.error("Health check database probe failed: %s", exc)
        return JsonResponse(
            {"status": "unhealthy", "database": "unavailable"},
            status=503,
        )
    return JsonResponse({"status": "ok"})


class OrganizationListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "organization_create"

    def get_queryset(self):
        return (
            Membership.objects.using("default")
            .filter(user_id=self.request.user.pk, tenant__is_active=True)
            .select_related("tenant")
        )

    def get_serializer_class(self):
        if self.request.method == "POST":
            return OrganizationCreateSerializer
        return OrganizationMembershipSerializer

    def get_throttles(self):
        if self.request.method == "POST":
            return [ScopedRateThrottle()]
        return []

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        name = serializer.validated_data["name"]
        slug = slugify(name)[:63].strip("-")
        if not slug:
            return Response(
                {"name": ["Enter a name that can be used to create an organization slug."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        domain = self._organization_domain(slug)
        if isinstance(domain, Response):
            return domain

        existing = Tenant.objects.using("default").filter(slug=slug).first()
        if existing:
            return self._retry_or_conflict(existing, name)

        alias = f"tenant_{uuid.uuid4().hex}"
        try:
            with transaction.atomic(using="default"):
                tenant = Tenant.objects.using("default").create(
                    name=name,
                    slug=slug,
                    domain=domain,
                    database_alias=alias,
                    is_active=False,
                    provisioning_status=Tenant.ProvisioningStatus.PENDING,
                )
                membership = Membership.objects.using("default").create(
                    user=request.user,
                    tenant=tenant,
                    role=Membership.Role.ADMIN,
                )
        except IntegrityError:
            existing = Tenant.objects.using("default").filter(slug=slug).first()
            if existing:
                return self._retry_or_conflict(existing, name)
            return Response(
                {"detail": "An organization with this domain already exists."},
                status=status.HTTP_409_CONFLICT,
            )

        return self._provision_and_respond(tenant, membership, created=True)

    def _organization_domain(self, slug):
        suffix = settings.TENANT_DOMAIN_SUFFIX.strip(".").lower()
        if not suffix or any(
            not label
            or len(label) > 63
            or not label[0].isalnum()
            or not label[-1].isalnum()
            or any(not (char.isalnum() or char == "-") for char in label)
            for label in suffix.split(".")
        ):
            raise ImproperlyConfigured("TENANT_DOMAIN_SUFFIX must be a valid DNS suffix.")
        domain = f"{slug}.{suffix}"
        if len(domain) > 253:
            return Response(
                {"name": ["The organization name is too long to create a tenant domain."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return domain

    def _retry_or_conflict(self, tenant, name):
        membership = (
            Membership.objects.using("default")
            .filter(
                user_id=self.request.user.pk,
                tenant=tenant,
                role=Membership.Role.ADMIN,
            )
            .first()
        )
        if (
            tenant.provisioning_status == Tenant.ProvisioningStatus.PENDING
            and membership
            and tenant.name == name
        ):
            return self._provision_and_respond(tenant, membership, created=False)
        return Response(
            {"detail": "An organization with this slug already exists."},
            status=status.HTTP_409_CONFLICT,
        )

    def _provision_and_respond(self, tenant, membership, *, created):
        with transaction.atomic(using="default"):
            tenant = Tenant.objects.using("default").select_for_update().get(pk=tenant.pk)
            membership = Membership.objects.using("default").get(pk=membership.pk)
            if tenant.is_active and tenant.provisioning_status == Tenant.ProvisioningStatus.READY:
                return Response(
                    OrganizationMembershipSerializer(membership).data,
                    status=status.HTTP_200_OK,
                )
            if tenant.provisioning_status != Tenant.ProvisioningStatus.PENDING:
                return Response(
                    {"detail": "Organization provisioning is in an invalid state."},
                    status=status.HTTP_409_CONFLICT,
                )
            if tenant.is_active:
                tenant.is_active = False
                tenant.save(using="default", update_fields=["is_active"])

        try:
            provision_tenant_database(tenant)
            with transaction.atomic(using="default"):
                tenant = Tenant.objects.using("default").select_for_update().get(pk=tenant.pk)
                free_plan = Plan.objects.using("default").get(code="free", is_active=True)
                Subscription.objects.using("default").get_or_create(
                    tenant=tenant,
                    defaults={
                        "plan": free_plan,
                        "status": Subscription.Status.ACTIVE,
                        "starts_at": timezone.now(),
                    },
                )
                if tenant.provisioning_status == Tenant.ProvisioningStatus.PENDING:
                    tenant.is_active = True
                    tenant.provisioning_status = Tenant.ProvisioningStatus.READY
                    tenant.save(
                        using="default",
                        update_fields=["is_active", "provisioning_status"],
                    )
                elif not (
                    tenant.is_active
                    and tenant.provisioning_status == Tenant.ProvisioningStatus.READY
                ):
                    raise RuntimeError("Tenant provisioning completed in an invalid state.")
                membership = Membership.objects.using("default").get(pk=membership.pk)
        except Exception as exc:
            logger.error(
                "Tenant provisioning failed for tenant %s (%s).",
                tenant.pk,
                type(exc).__name__,
            )
            return Response(
                {
                    "detail": (
                        "Organization provisioning is incomplete. Retry this request "
                        "with the same organization name."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(
            OrganizationMembershipSerializer(membership).data,
            status=response_status,
        )


class MemberListView(ListAPIView):
    permission_classes = [IsAuthenticated, CanManageMembers]
    serializer_class = MembershipSerializer

    def get_queryset(self):
        return (
            Membership.objects.using("default")
            .filter(tenant=self.request.tenant)
            .select_related("user")
            .order_by("created_at")
        )


class MemberDetailView(APIView):
    permission_classes = [IsAuthenticated, CanManageMembers]

    def get_object(self, pk):
        membership = (
            Membership.objects.using("default")
            .filter(pk=pk, tenant=self.request.tenant)
            .select_related("user", "tenant")
            .first()
        )
        if not membership:
            raise Http404
        return membership

    def patch(self, request, pk):
        target = self.get_object(pk)
        serializer = MemberRoleUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_role = serializer.validated_data["role"]
        caller_role = request.membership.role

        if target.role == Membership.Role.OWNER and caller_role != Membership.Role.OWNER:
            raise PermissionDenied("Only organization owners can modify owner roles.")

        if new_role == Membership.Role.OWNER and caller_role != Membership.Role.OWNER:
            has_owner = (
                Membership.objects.using("default")
                .filter(tenant=request.tenant, role=Membership.Role.OWNER)
                .exists()
            )
            if has_owner:
                raise PermissionDenied("Only organization owners can promote members to owner.")

        if target.role == Membership.Role.OWNER and new_role != Membership.Role.OWNER:
            owners_count = (
                Membership.objects.using("default")
                .filter(tenant=request.tenant, role=Membership.Role.OWNER)
                .count()
            )
            if owners_count <= 1:
                raise PermissionDenied("Cannot demote the last owner of the organization.")

        old_role = target.role
        target.role = new_role
        target.save(using="default", update_fields=["role"])

        log_audit_event(
            tenant=request.tenant,
            action=AuditLog.Action.MEMBER_ROLE_CHANGED,
            resource_type="membership",
            resource_id=target.pk,
            actor=request.user,
            metadata={
                "member_user_id": target.user_id,
                "old_role": old_role,
                "new_role": new_role,
            },
            request=request,
        )
        return Response(MembershipSerializer(target).data)

    def delete(self, request, pk):
        target = self.get_object(pk)
        caller_role = request.membership.role

        if target.role == Membership.Role.OWNER and caller_role != Membership.Role.OWNER:
            raise PermissionDenied("Only organization owners can remove an owner.")

        if target.role == Membership.Role.OWNER:
            owners_count = (
                Membership.objects.using("default")
                .filter(tenant=request.tenant, role=Membership.Role.OWNER)
                .count()
            )
            if owners_count <= 1:
                raise PermissionDenied("Cannot remove the last owner of the organization.")

        member_id = target.pk
        member_user_id = target.user_id
        target.delete()

        log_audit_event(
            tenant=request.tenant,
            action=AuditLog.Action.MEMBER_REMOVED,
            resource_type="membership",
            resource_id=member_id,
            actor=request.user,
            metadata={"removed_user_id": member_user_id},
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationListCreateView(ListCreateAPIView):
    permission_classes = [IsAuthenticated, CanManageMembers]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return InvitationCreateSerializer
        return InvitationSerializer

    def get_queryset(self):
        qs = (
            Invitation.objects.using("default")
            .filter(tenant=self.request.tenant)
            .select_related("invited_by")
        )
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        role = serializer.validated_data.get("role", Membership.Role.MEMBER)

        caller_role = request.membership.role
        if role == Membership.Role.OWNER and caller_role != Membership.Role.OWNER:
            raise PermissionDenied("Only organization owners can invite new owners.")

        existing_member = (
            Membership.objects.using("default")
            .filter(tenant=request.tenant, user__email__iexact=email)
            .exists()
        )
        if existing_member:
            return Response(
                {"detail": "A user with this email is already a member of this organization."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing_invite = (
            Invitation.objects.using("default")
            .filter(
                tenant=request.tenant,
                email__iexact=email,
                status=Invitation.Status.PENDING,
            )
            .first()
        )
        if existing_invite:
            if existing_invite.expires_at > timezone.now():
                return Response(
                    {"detail": "An active invitation already exists for this email."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            existing_invite.status = Invitation.Status.EXPIRED
            existing_invite.save(using="default", update_fields=["status"])

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = timezone.now() + timezone.timedelta(days=7)

        invitation = Invitation.objects.using("default").create(
            tenant=request.tenant,
            email=email,
            role=role,
            token_hash=token_hash,
            invited_by=request.user,
            status=Invitation.Status.PENDING,
            expires_at=expires_at,
        )

        log_audit_event(
            tenant=request.tenant,
            action=AuditLog.Action.INVITATION_CREATED,
            resource_type="invitation",
            resource_id=invitation.pk,
            actor=request.user,
            metadata={"email": email, "role": role},
            request=request,
        )

        invitation.token = raw_token
        return Response(
            InvitationSerializer(invitation).data,
            status=status.HTTP_201_CREATED,
        )


class InvitationRevokeView(APIView):
    permission_classes = [IsAuthenticated, CanManageMembers]

    def post(self, request, pk):
        invitation = (
            Invitation.objects.using("default").filter(pk=pk, tenant=request.tenant).first()
        )
        if not invitation:
            raise Http404

        if (
            invitation.role == Membership.Role.OWNER
            and request.membership.role != Membership.Role.OWNER
        ):
            raise PermissionDenied("Only organization owners can revoke owner invitations.")

        if invitation.status != Invitation.Status.PENDING:
            return Response(
                {"detail": f"Cannot revoke an invitation with status '{invitation.status}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invitation.status = Invitation.Status.REVOKED
        invitation.revoked_at = timezone.now()
        invitation.save(using="default", update_fields=["status", "revoked_at"])

        log_audit_event(
            tenant=request.tenant,
            action=AuditLog.Action.INVITATION_REVOKED,
            resource_type="invitation",
            resource_id=invitation.pk,
            actor=request.user,
            metadata={"email": invitation.email},
            request=request,
        )

        return Response(InvitationSerializer(invitation).data, status=status.HTTP_200_OK)


class InvitationAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = InvitationAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data["token"].strip()
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        invitation = (
            Invitation.objects.using("default")
            .select_related("tenant")
            .filter(token_hash=token_hash)
            .first()
        )
        if not invitation or invitation.status != Invitation.Status.PENDING:
            return Response(
                {"detail": "Invalid or already used invitation token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if invitation.expires_at <= timezone.now():
            invitation.status = Invitation.Status.EXPIRED
            invitation.save(using="default", update_fields=["status"])
            return Response(
                {"detail": "Invitation has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_email = (request.user.email or "").strip().lower()
        if not user_email or user_email != invitation.email.strip().lower():
            return Response(
                {
                    "detail": (
                        f"This invitation was sent to {invitation.email}. "
                        f"Please sign in with that email address."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        already_member = (
            Membership.objects.using("default")
            .filter(tenant=invitation.tenant, user=request.user)
            .exists()
        )
        if already_member:
            return Response(
                {"detail": "You are already a member of this organization."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic(using="default"):
            inv = Invitation.objects.using("default").select_for_update().get(pk=invitation.pk)
            if inv.status != Invitation.Status.PENDING:
                return Response(
                    {"detail": "Invalid or already used invitation token."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            membership = Membership(
                tenant=inv.tenant,
                user=request.user,
                role=inv.role,
            )
            try:
                membership.save(using="default")
            except Exception as exc:
                message = getattr(exc, "message", str(exc))
                return Response(
                    {"detail": message},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            inv.status = Invitation.Status.ACCEPTED
            inv.accepted_at = timezone.now()
            inv.save(using="default", update_fields=["status", "accepted_at"])

        log_audit_event(
            tenant=invitation.tenant,
            action=AuditLog.Action.INVITATION_ACCEPTED,
            resource_type="invitation",
            resource_id=invitation.pk,
            actor=request.user,
            metadata={"email": invitation.email, "role": invitation.role},
            request=request,
        )
        log_audit_event(
            tenant=invitation.tenant,
            action=AuditLog.Action.MEMBER_ADDED,
            resource_type="membership",
            resource_id=membership.pk,
            actor=request.user,
            metadata={"user_id": request.user.pk, "role": membership.role},
            request=request,
        )

        return Response(
            {
                "detail": f"Successfully joined {invitation.tenant.name}.",
                "membership": MembershipSerializer(membership).data,
            },
            status=status.HTTP_200_OK,
        )


class AuditLogListView(ListAPIView):
    permission_classes = [IsAuthenticated, IsOrganizationAdmin]
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        qs = (
            AuditLog.objects.using("default")
            .filter(tenant=self.request.tenant)
            .select_related("actor")
        )
        action = self.request.query_params.get("action")
        if action:
            qs = qs.filter(action=action)
        resource_type = self.request.query_params.get("resource_type")
        if resource_type:
            qs = qs.filter(resource_type=resource_type)
        return qs
