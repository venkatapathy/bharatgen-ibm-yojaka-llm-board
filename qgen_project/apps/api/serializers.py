from rest_framework import serializers
from apps.core.models import User, Organization, ModelConfig
from apps.pdf_module.models import PDFContext
from apps.pyq_module.models import PYQModule, Question
from apps.prompt_module.models import PromptTemplate
from apps.question_generation.models import BatchRun, BatchRunItem


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Organization
        fields = ['id', 'name', 'slug', 'is_active', 'created_at']


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model  = User
        fields = ['id', 'username', 'email', 'role', 'organization',
                  'is_active_member', 'created_at']
        read_only_fields = ['created_at']


class ModelConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ModelConfig
        fields = ['id', 'name', 'provider', 'llm_model_id', 'embed_model_id',
                  'temperature', 'max_tokens', 'is_default']


class PDFContextSerializer(serializers.ModelSerializer):
    chunk_count = serializers.IntegerField(source='chunk_count', read_only=True)

    class Meta:
        model  = PDFContext
        fields = ['id', 'name', 'description', 'strategy', 'status',
                  'chunk_count', 'created_at']


class PYQModuleSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PYQModule
        fields = ['id', 'name', 'description', 'status', 'created_at']


class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Question
        fields = ['id', 'question_type', 'bloom', 'marks', 'is_generated',
                  'question_text', 'reference_answer', 'rubrics', 'topic',
                  'options', 'pyq_module', 'batch_run', 'created_at']


class PromptTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PromptTemplate
        fields = ['id', 'name', 'description', 'system_prompt', 'user_prompt',
                  'version', 'is_active', 'updated_at']


class BatchRunItemSerializer(serializers.ModelSerializer):
    class Meta:
        model  = BatchRunItem
        fields = ['id', 'question_type', 'bloom', 'marks', 'count', 'status']


class BatchRunSerializer(serializers.ModelSerializer):
    items    = BatchRunItemSerializer(many=True, read_only=True)
    progress = serializers.IntegerField(read_only=True)

    class Meta:
        model  = BatchRun
        fields = ['id', 'name', 'topic', 'status', 'progress',
                  'rag_top_k', 'pyq_shots', 'created_at', 'completed_at', 'items']
