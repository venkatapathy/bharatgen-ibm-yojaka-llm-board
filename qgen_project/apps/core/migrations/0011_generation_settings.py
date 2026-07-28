# Generated manually for GenerationSettings singleton

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('prompt_module', '0004_prompttemplate_topic_grounding_and_more'),
        ('core', '0010_pdf_indexing_settings'),
    ]

    operations = [
        migrations.CreateModel(
            name='GenerationSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rag_top_k', models.IntegerField(default=5, help_text='Number of PDF chunks retrieved per question.')),
                ('pyq_shots', models.IntegerField(default=3, help_text='Number of PYQ examples injected as few-shot style.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('hindi_prompt', models.ForeignKey(blank=True, help_text='Prompt used when output language is Hindi.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='prompt_module.prompttemplate')),
                ('model_config', models.ForeignKey(blank=True, help_text='LLM config used for generation.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='core.modelconfig')),
                ('prompt', models.ForeignKey(blank=True, help_text='Default prompt template (English / fallback).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='prompt_module.prompttemplate')),
            ],
            options={
                'verbose_name': 'Generation settings',
                'verbose_name_plural': 'Generation settings',
            },
        ),
    ]
