from django.db import migrations, models
import django.db.models.deletion


def copy_legacy_reranker(apps, schema_editor):
    PDFIndexingSettings = apps.get_model("core", "PDFIndexingSettings")
    GenerationSettings = apps.get_model("core", "GenerationSettings")
    ModelConfig = apps.get_model("core", "ModelConfig")

    default = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    for pdf in PDFIndexingSettings.objects.all():
        if pdf.reranker_model:
            continue
        cfg_id = pdf.reranker_config_id
        if cfg_id:
            cfg = ModelConfig.objects.filter(pk=cfg_id).first()
            if cfg and (cfg.reranker_model or "").strip():
                pdf.reranker_model = cfg.reranker_model.strip()
                pdf.save(update_fields=["reranker_model"])
                continue
        pdf.reranker_model = default
        pdf.save(update_fields=["reranker_model"])

    for gen in GenerationSettings.objects.all():
        if not (gen.rag_reranker_model or "").strip():
            gen.rag_reranker_model = default
            gen.save(update_fields=["rag_reranker_model"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_generation_settings_user_feedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="pdfindexingsettings",
            name="reranker_model",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    (
                        "cross-encoder/ms-marco-MiniLM-L-6-v2",
                        "MiniLM-L-6-v2 (recommended — light)",
                    ),
                    (
                        "cross-encoder/ms-marco-TinyBERT-L-2-v2",
                        "TinyBERT-L-2 (lighter, slightly weaker)",
                    ),
                ],
                default="",
                help_text="Cross-encoder used to rerank retrieved PDF chunks.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="generationsettings",
            name="rag_reranker_model",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "None"),
                    (
                        "cross-encoder/ms-marco-MiniLM-L-6-v2",
                        "MiniLM-L-6-v2 (recommended — light)",
                    ),
                    (
                        "cross-encoder/ms-marco-TinyBERT-L-2-v2",
                        "TinyBERT-L-2 (lighter, slightly weaker)",
                    ),
                ],
                default="cross-encoder/ms-marco-MiniLM-L-6-v2",
                help_text="Cross-encoder used to rerank RAG chunks during question generation.",
                max_length=128,
            ),
        ),
        migrations.AlterField(
            model_name="pdfindexingsettings",
            name="reranker_config",
            field=models.ForeignKey(
                blank=True,
                help_text="Deprecated: use reranker_model. Kept for migration compatibility.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="core.modelconfig",
            ),
        ),
        migrations.RunPython(copy_legacy_reranker, migrations.RunPython.noop),
    ]
