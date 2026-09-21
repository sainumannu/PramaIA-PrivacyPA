import React, { useEffect, useState } from 'react';
import { Alert, Box, Chip, Step, StepLabel, Stepper, Table, TableBody, TableCell, TableHead, TableRow, Typography } from '@mui/material';
import RocketLaunchIcon from '@mui/icons-material/RocketLaunch';
import { EntityCrudPage, FieldSpec, StatusChip, postAction } from '../components/EntityCrud';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';

const PR = '/api/privacypa/projects';
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : '—');
const stamp = (iso: string | null) => (iso ? iso.replace('T', ' ').slice(0, 16) : '—');

const FLAGS = ['uses_cloud', 'uses_ai', 'profiles_people', 'automated_decisions', 'special_data', 'criminal_data', 'minors', 'systematic_monitoring', 'large_scale'];
const REVIEW_PERMISSION: Record<string, string> = {
  PRIVACY_REVIEW: 'project.review_privacy', ICT_REVIEW: 'project.review_ict', DPO_REVIEW: 'project.review_dpo',
  APPROVAL: 'project.approve', POST_REVIEW: 'project.review_privacy',
};
const GENERATION_STATES = ['DPIA', 'APPROVAL', 'GO_LIVE', 'POST_REVIEW', 'CLOSED'];
const TERMINAL = ['CLOSED', 'REJECTED', 'CANCELLED'];

// Le fasi che il progetto attraversa: DPO e DPIA solo se le caratteristiche dichiarate le richiedono.
const workflowSteps = (project: any): string[] => [
  'PROPOSAL', 'PRIVACY_REVIEW', 'ICT_REVIEW', ...(project.assessment?.dpo_review_required ? ['DPO_REVIEW'] : []),
  ...(project.dpia_screening_required ? ['DPIA'] : []), 'APPROVAL', 'GO_LIVE', 'POST_REVIEW', 'CLOSED',
];

const ProjectDetail: React.FC<{ row: any }> = ({ row }) => {
  const { t } = useTranslation('privacypa');
  const [project, setProject] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiClient.get<any>(`${PR}/${row.id}`).then(setProject).catch((err) => setError(err.message));
  }, [row.id, row.row_version]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!project) return null;
  const steps = workflowSteps(project);
  const active = steps.indexOf(project.status);
  const raised = FLAGS.filter((f) => project[f]);
  const artifactLabel = (type: string) => (type.startsWith('task:') ? `${t('projects.artifacts.task')}: ${t(`projects.artifacts.${type.slice(5)}`)}` : t(`projects.artifacts.${type}`));

  return (
    <>
      {TERMINAL.includes(project.status) && project.status !== 'CLOSED'
        ? <Chip color="error" label={t(`wf.enum.projectStatus.${project.status}`)} sx={{ alignSelf: 'flex-start' }} />
        : (
          <Stepper activeStep={active < 0 ? 0 : active} alternativeLabel sx={{ overflowX: 'auto' }}>
            {steps.map((s) => <Step key={s}><StepLabel>{t(`wf.enum.projectStatus.${s}`)}</StepLabel></Step>)}
          </Stepper>
        )}
      <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{project.description || '—'}</Typography>
      <Typography variant="subtitle2">{t('projects.characteristics')}</Typography>
      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
        {raised.length === 0 && <Typography variant="body2" color="text.secondary">{t('projects.no_characteristics')}</Typography>}
        {raised.map((f) => <Chip key={f} size="small" label={t(`wf.fields.${f}`)} color={f === 'uses_cloud' ? 'default' : 'warning'} />)}
      </Box>
      <Typography variant="caption" color="text.secondary">
        {project.dpia_screening_required ? t('projects.assessment_risky') : t('projects.assessment_plain')}
        {project.go_live_date ? ` · ${t('projects.go_live')}: ${day(project.go_live_date)} · ${t('projects.post_review')}: ${day(project.post_review_due_date)}` : ''}
      </Typography>

      <Typography variant="subtitle2" sx={{ mt: 1 }}>{t('projects.reviews')}</Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>{t('projects.cycle')}</TableCell><TableCell>{t('projects.stage')}</TableCell><TableCell>{t('wf.fields.outcome')}</TableCell>
            <TableCell>{t('projects.reviewer')}</TableCell><TableCell>{t('projects.when')}</TableCell><TableCell>{t('wf.fields.notes')}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {project.reviews.length === 0 && <TableRow><TableCell colSpan={6}>{t('wf.empty')}</TableCell></TableRow>}
          {project.reviews.map((r: any) => (
            <TableRow key={r.id}>
              <TableCell>{r.cycle}</TableCell><TableCell>{t(`wf.enum.reviewStage.${r.stage}`)}</TableCell>
              <TableCell><StatusChip enumName="reviewOutcome" value={r.outcome} color={r.outcome === 'APPROVED' ? 'success' : r.outcome === 'REJECTED' ? 'error' : 'warning'} /></TableCell>
              <TableCell>{r.reviewer_user_id}</TableCell><TableCell>{stamp(r.decided_at)}</TableCell><TableCell sx={{ whiteSpace: 'pre-wrap' }}>{r.notes || '—'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <Typography variant="subtitle2" sx={{ mt: 1 }}>{t('projects.artifacts.title')}</Typography>
      {project.artifacts.length === 0 && <Typography variant="body2" color="text.secondary">{t('projects.artifacts.none')}</Typography>}
      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
        {project.artifacts.map((a: any) => <Chip key={a.entity_id} size="small" variant="outlined" label={artifactLabel(a.entity_type)} />)}
      </Box>
    </>
  );
};

export const ProjectsPage: React.FC = () => {
  const { can } = usePermissions();
  const fields: FieldSpec[] = [
    { name: 'title', type: 'text', required: true }, { name: 'description', type: 'textarea' },
    { name: 'proposing_unit_id', type: 'unit', required: true }, { name: 'project_owner_id', type: 'text', required: true },
    { name: 'supplier_id', type: 'ref', refEndpoint: '/api/privacypa/parties', refLabel: (r) => r.legal_name },
    { name: 'confidentiality_level', type: 'select', options: ['INTERNAL', 'RESTRICTED', 'HIGHLY_RESTRICTED'], enumName: 'confidentiality' },
    ...FLAGS.map((name) => ({ name, type: 'boolean' as const })),
  ];
  return (
    <EntityCrudPage
      page="projects" icon={<RocketLaunchIcon color="primary" />} endpoint={PR} perm="project" allowDelete annotate="project"
      canEditRow={(r) => r.status === 'PROPOSAL'}
      fields={fields}
      columns={[
        { key: 'code', render: (r) => r.code }, { key: 'title', render: (r) => r.title },
        { key: 'status', render: (r) => <StatusChip enumName="projectStatus" value={r.status} color={r.status === 'CLOSED' ? 'success' : r.status === 'REJECTED' || r.status === 'CANCELLED' ? 'error' : 'default'} /> },
        { key: 'unit', render: (r, units) => units.find((u) => u.id === r.proposing_unit_id)?.name || '—' },
        { key: 'owner', render: (r) => r.project_owner_id },
        { key: 'dpia', render: (r) => (r.dpia_screening_required ? <Chip size="small" color="warning" label="DPIA" /> : '—') },
        { key: 'go_live', render: (r) => day(r.go_live_date) },
      ]}
      detail={(row) => <ProjectDetail row={row} />}
      actions={[
        { key: 'submit', permission: 'project.submit', visible: (r) => r.status === 'PROPOSAL', run: postAction(PR, '/submit') },
        { key: 'review', notes: 'optional', visible: (r) => Boolean(REVIEW_PERMISSION[r.status]) && can(REVIEW_PERMISSION[r.status]),
          fields: [{ name: 'outcome', type: 'select', options: ['APPROVED', 'CHANGES_REQUESTED', 'REJECTED'], enumName: 'reviewOutcome', required: true },
            { name: 'go_live_date', type: 'date', hint: 'wf.hints.go_live_date' }],
          run: (r, v) => apiClient.post(`${PR}/${r.id}/reviews`, { outcome: v.outcome, notes: v.notes, go_live_date: r.status === 'APPROVAL' ? v.go_live_date : null }, { version: r.row_version }).then(() => undefined) },
        { key: 'dpia', permission: 'project.edit', visible: (r) => r.status === 'DPIA' && r.dpia_screening_required && !r.dpia_id, run: postAction(PR, '/dpia') },
        { key: 'advance', permission: 'project.edit', visible: (r) => r.status === 'GO_LIVE' || (r.status === 'DPIA' && Boolean(r.dpia_id)), run: postAction(PR, '/advance') },
        { key: 'generate', permission: 'project.generate', visible: (r) => GENERATION_STATES.includes(r.status),
          run: (r) => apiClient.post(`${PR}/${r.id}/generate`, {}).then(() => undefined) },
        { key: 'cancel', permission: 'project.edit', color: 'warning', notes: 'required', visible: (r) => !TERMINAL.includes(r.status),
          run: (r, v) => apiClient.post(`${PR}/${r.id}/cancel`, { notes: v.notes }, { version: r.row_version }).then(() => undefined) },
      ]}
    />
  );
};
