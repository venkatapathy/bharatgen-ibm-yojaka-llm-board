from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("pyq_module", "0004_human_review_dataset"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                UPDATE pyq_module_question SET pyq_examples = '[]'::jsonb WHERE pyq_examples IS NULL;
                UPDATE pyq_module_question SET rag_chunks = '[]'::jsonb WHERE rag_chunks IS NULL;
                UPDATE pyq_module_question SET user_decision = 'pending' WHERE user_decision IS NULL OR user_decision = '';
                UPDATE pyq_module_question SET user_feedback = '' WHERE user_feedback IS NULL;

                ALTER TABLE pyq_module_question
                    ALTER COLUMN pyq_examples SET DEFAULT '[]'::jsonb,
                    ALTER COLUMN rag_chunks SET DEFAULT '[]'::jsonb,
                    ALTER COLUMN user_decision SET DEFAULT 'pending',
                    ALTER COLUMN user_feedback SET DEFAULT '';
            """,
            reverse_sql="""
                ALTER TABLE pyq_module_question
                    ALTER COLUMN pyq_examples DROP DEFAULT,
                    ALTER COLUMN rag_chunks DROP DEFAULT,
                    ALTER COLUMN user_decision DROP DEFAULT,
                    ALTER COLUMN user_feedback DROP DEFAULT;
            """,
        ),
    ]
