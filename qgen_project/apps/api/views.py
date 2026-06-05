from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.core.models import User, Organization, ModelConfig
from apps.core.permissions import IsSuperUser, IsOrgUser
from apps.pdf_module.models import PDFContext
from apps.pyq_module.models import PYQModule, Question
from apps.prompt_module.models import PromptTemplate
from apps.question_generation.models import BatchRun
from .serializers import (
    OrganizationSerializer, UserSerializer, ModelConfigSerializer,
    PDFContextSerializer, PYQModuleSerializer, QuestionSerializer,
    PromptTemplateSerializer, BatchRunSerializer,
)


class OrganizationViewSet(viewsets.ModelViewSet):
    queryset         = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsSuperUser]


class UserViewSet(viewsets.ModelViewSet):
    serializer_class   = UserSerializer
    permission_classes = [IsOrgUser]

    def get_queryset(self):
        user = self.request.user
        if user.role == User.Role.SUPERUSER:
            return User.objects.all()
        return User.objects.filter(organization=user.organization)

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        return Response(UserSerializer(request.user).data)


class PDFContextViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class   = PDFContextSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return PDFContext.objects.filter(
            organization=self.request.user.organization, status='ready')


class PYQModuleViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class   = PYQModuleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return PYQModule.objects.filter(
            organization=self.request.user.organization, status='ready')


class QuestionViewSet(viewsets.ModelViewSet):
    serializer_class   = QuestionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = Question.objects.all()
        if pyq_id := self.request.query_params.get('pyq_module'):
            qs = qs.filter(pyq_module_id=pyq_id)
        if run_id := self.request.query_params.get('batch_run'):
            qs = qs.filter(batch_run_id=run_id)
        return qs


class PromptTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset           = PromptTemplate.objects.all()
    serializer_class   = PromptTemplateSerializer
    permission_classes = [permissions.IsAuthenticated]


class BatchRunViewSet(viewsets.ModelViewSet):
    serializer_class   = BatchRunSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return BatchRun.objects.filter(created_by=self.request.user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        run = self.get_object()
        if run.celery_task_id:
            from qgen.celery import app as celery_app
            celery_app.control.revoke(run.celery_task_id, terminate=True)
        run.status = 'failed'
        run.save(update_fields=['status'])
        return Response({'status': 'cancelled'})
