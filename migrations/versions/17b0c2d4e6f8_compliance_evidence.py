"""Task-scoped human-confirmed compliance evidence and workflow contract."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '17b0c2d4e6f8'
down_revision = '0f4e8b6a9c21'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tasks', sa.Column('workflow_contract_version', sa.String(32), nullable=False, server_default='legacy/1.0'))
    value = sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')
    op.create_table('compliance_evidence_records',
        sa.Column('evidence_id', sa.String(64), primary_key=True),
        sa.Column('task_id', sa.String(64), sa.ForeignKey('tasks.task_id', ondelete='CASCADE'), nullable=False),
        sa.Column('task_revision', sa.Integer(), nullable=False),
        sa.Column('quote_id', sa.String(64), sa.ForeignKey('quotes.quote_id'), nullable=False),
        sa.Column('control_code', sa.String(64), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('previous_evidence_id', sa.String(64), sa.ForeignKey('compliance_evidence_records.evidence_id'), unique=True),
        sa.Column('facts', value, nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('confirmed_by', sa.String(128), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_compliance_evidence_records_task_id', 'compliance_evidence_records', ['task_id'])
    op.create_index('ix_compliance_evidence_records_quote_id', 'compliance_evidence_records', ['quote_id'])
    op.create_table('compliance_evidence_files',
        sa.Column('file_id', sa.String(64), primary_key=True),
        sa.Column('evidence_id', sa.String(64), sa.ForeignKey('compliance_evidence_records.evidence_id', ondelete='CASCADE'), nullable=False),
        sa.Column('original_filename', sa.String(512), nullable=False),
        sa.Column('media_type', sa.String(128), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('storage_path', sa.Text(), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_compliance_evidence_files_evidence_id', 'compliance_evidence_files', ['evidence_id'])


def downgrade():
    op.drop_table('compliance_evidence_files')
    op.drop_table('compliance_evidence_records')
    op.drop_column('tasks', 'workflow_contract_version')
