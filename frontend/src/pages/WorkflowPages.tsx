import React, { useEffect, useState } from 'react';
import { Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Typography } from '@mui/material';
import GavelIcon from '@mui/icons-material/Gavel';
import BuildIcon from '@mui/icons-material/Build';
import ReportIcon from '@mui/icons-material/ReportProblem';
import PersonSearchIcon from '@mui/icons-material/PersonSearch';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import {
  ActionSpec, EntityCrudPage, FieldSpec, FormFields, StatusChip, Values, bodyFromValues, emptyFor, missingRequired, postAction,
} from '../components/EntityCrud';
import { PersonName } from '../components/PortalPeople';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';

const unitName = (units: { id: string; name: string }[], id: string | null) => units.find((u) => u.id === id)?.name || '—';
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : '—');
const stamp = (iso: string | null) => (iso ? iso.replace('T', ' ').slice(0, 16) : '—');
const setStatus = (endpoint: string, status: string) => (row: any) =>
  apiClient.post(`${endpoint}/${row.id}/status`, { status }, { version: row.row_version }).then(() => undefined);
const isPast = (iso: string | null) => Boolean(iso && new Date(iso) < new Date());

// ---------------------------------------------------------------- riferimenti normativi (§13)

const LEGAL = '/api/privacypa/legal-references';
const LEGAL_TYPES = ['LAW', 'LEGISLATIVE_DECREE', 'REGULATION', 'DIRECTIVE', 'GUIDELINE', 'MUNICIPAL_ACT', 'OTHER'];

export const LegalReferencesPage: React.FC = () => (
  <EntityCrudPage
    page="legalref" icon={<GavelIcon color="primary" />} endpoint={LEGAL} perm="legalref" allowDelete
    fields={[
      { name: 'source_type', type: 'select', options: LEGAL_TYPES, enumName: 'legalSource', required: true },
      { name: 'title', type: 'text', required: true }, { name: 'number', type: 'text' }, { name: 'year', type: 'number' },
      { name: 'article', type: 'text' }, { name: 'url', type: 'text' }, { name: 'notes', type: 'textarea' },
    ]}
    columns={[
      { key: 'citation', render: (r) => r.citation },
      { key: 'source_type', render: (r) => <StatusChip enumName="legalSource" value={r.source_type} /> },
      { key: 'url', render: (r) => (r.url ? <a href={r.url} target="_blank" rel="noopener noreferrer">{r.url}</a> : '—') },
    ]}
  />
);

// ---------------------------------------------------------------- azioni correttive (§29)

const ACTIONS = '/api/privacypa/corrective-actions';
const closed = (r: any) => ['COMPLETED', 'CANCELLED'].includes(r.status);

export const CorrectiveActionsPage: React.FC = () => (
  <EntityCrudPage
    page="actions" icon={<BuildIcon color="primary" />} endpoint={ACTIONS} perm="action" allowDelete annotate="corrective_action"
    canEditRow={(r) => !closed(r)}
    fields={[
      { name: 'source_type', type: 'select', options: ['MANUAL', 'DPO_RECOMMENDATION', 'SECURITY_ASSESSMENT'], enumName: 'actionSource', required: true, createOnly: true },
      { name: 'title', type: 'text', required: true }, { name: 'description', type: 'textarea', required: true },
      { name: 'priority', type: 'select', options: ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'], enumName: 'priority', required: true },
      { name: 'owner_user_id', type: 'person', required: true }, { name: 'organizational_unit_id', type: 'unit' },
      { name: 'due_date', type: 'date', required: true },
    ]}
    toBody={(body, editing) => (editing ? { ...body, source_type: editing.source_type, source_id: editing.source_id, evidence_document_id: editing.evidence_document_id } : body)}
    columns={[
      { key: 'code', render: (r) => r.code }, { key: 'title', render: (r) => r.title },
      { key: 'priority', render: (r) => <StatusChip enumName="priority" value={r.priority} color={r.priority === 'CRITICAL' || r.priority === 'HIGH' ? 'warning' : 'default'} /> },
      { key: 'owner', render: (r) => <PersonName id={r.owner_user_id} /> },
      { key: 'due_date', render: (r) => <span style={{ color: !closed(r) && isPast(r.due_date) ? '#ef4444' : undefined }}>{day(r.due_date)}</span> },
      { key: 'source', render: (r) => <StatusChip enumName="actionSource" value={r.source_type} /> },
      { key: 'status', render: (r) => <StatusChip enumName="actionStatus" value={r.status} color={r.status === 'COMPLETED' ? 'success' : 'default'} /> },
    ]}
    actions={[
      { key: 'start', permission: 'action.edit', visible: (r) => r.status === 'OPEN', run: setStatus(ACTIONS, 'IN_PROGRESS') },
      { key: 'complete', permission: 'action.edit', color: 'success', visible: (r) => !closed(r), notes: 'required',
        run: (r, v) => apiClient.post(`${ACTIONS}/${r.id}/status`, { status: 'COMPLETED', notes: v.notes }, { version: r.row_version }).then(() => undefined) },
      { key: 'cancel', permission: 'action.edit', color: 'warning', visible: (r) => !closed(r), notes: 'required',
        run: (r, v) => apiClient.post(`${ACTIONS}/${r.id}/status`, { status: 'CANCELLED', notes: v.notes }, { version: r.row_version }).then(() => undefined) },
      { key: 'reopen', permission: 'action.edit', visible: closed, run: (r) => apiClient.post(`${ACTIONS}/${r.id}/status`, { status: r.status === 'COMPLETED' ? 'IN_PROGRESS' : 'OPEN' }, { version: r.row_version }).then(() => undefined) },
    ]}
  />
);

// ---------------------------------------------------------------- data breach (§26)

const BREACHES = '/api/privacypa/breaches';
const RISKS = ['NONE', 'LOW', 'MEDIUM', 'HIGH'];

export const BreachesPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const fields: FieldSpec[] = [
    { name: 'title', type: 'text', required: true }, { name: 'organizational_unit_id', type: 'unit', required: true },
    { name: 'aware_at', type: 'datetime', required: true, createOnly: true, hint: 'wf.hints.aware_at' },
    { name: 'detected_at', type: 'datetime' }, { name: 'reported_by', type: 'person' },
    { name: 'description', type: 'textarea', required: true }, { name: 'confidentiality_level', type: 'select', options: ['INTERNAL', 'RESTRICTED', 'HIGHLY_RESTRICTED'], enumName: 'confidentiality' },
    { name: 'confidentiality_affected', type: 'boolean' }, { name: 'integrity_affected', type: 'boolean' }, { name: 'availability_affected', type: 'boolean' },
    { name: 'approx_data_subjects', type: 'number' }, { name: 'approx_records', type: 'number' },
    { name: 'risk_level', type: 'select', options: RISKS, enumName: 'risk' }, { name: 'risk_rationale', type: 'textarea' },
    { name: 'notify_authority', type: 'tristate' }, { name: 'notify_authority_rationale', type: 'textarea' }, { name: 'authority_notified_at', type: 'datetime' },
    { name: 'notify_subjects', type: 'tristate' }, { name: 'notify_subjects_rationale', type: 'textarea' }, { name: 'subjects_notified_at', type: 'datetime' },
  ];
  const isClosed = (r: any) => r.status === 'CLOSED';
  const next = (key: string, status: string, from: string[], color?: ActionSpec['color'], permission = 'breach.edit'): ActionSpec => ({
    key, permission, color, visible: (r) => from.includes(r.status), run: setStatus(BREACHES, status),
  });
  return (
    <EntityCrudPage
      page="breaches" icon={<ReportIcon color="primary" />} endpoint={BREACHES} perm="breach" allowDelete annotate="data_breach"
      canEditRow={(r) => !isClosed(r)} fields={fields}
      columns={[
        { key: 'code', render: (r) => r.code }, { key: 'title', render: (r) => r.title },
        { key: 'unit', render: (r, u) => unitName(u, r.organizational_unit_id) },
        { key: 'aware_at', render: (r) => (
          <>
            {stamp(r.aware_at)}
            {r.aware_at_original && <Chip size="small" variant="outlined" sx={{ ml: 0.5 }} label={t('wf.corrected', { date: stamp(r.aware_at_original) })} />}
          </>
        ) },
        { key: 'elapsed', render: (r) => (
          <Chip size="small" color={!isClosed(r) && r.hours_since_aware >= 72 ? 'warning' : 'default'} label={t('wf.hours', { hours: String(Math.round(r.hours_since_aware)) })} />
        ) },
        { key: 'risk', render: (r) => <StatusChip enumName="risk" value={r.risk_level} /> },
        { key: 'authority', render: (r) => (r.notify_authority === null ? t('wf.tristate.undecided') : r.notify_authority ? (r.authority_notified_at ? t('wf.notified', { date: stamp(r.authority_notified_at) }) : t('wf.tristate.yes')) : t('wf.tristate.no')) },
        { key: 'status', render: (r) => <StatusChip enumName="breachStatus" value={r.status} color={isClosed(r) ? 'success' : 'warning'} /> },
      ]}
      actions={[
        next('assess', 'ASSESSING', ['OPEN', 'CONTAINED']), next('contain', 'CONTAINED', ['OPEN', 'ASSESSING']),
        next('close', 'CLOSED', ['OPEN', 'ASSESSING', 'CONTAINED'], 'success', 'breach.approve'), next('reopen', 'ASSESSING', ['CLOSED'], 'inherit', 'breach.approve'),
        {
          key: 'correct_aware_at', permission: 'breach.approve', fields: [{ name: 'aware_at', type: 'datetime', required: true }, { name: 'reason', type: 'textarea', required: true }],
          run: (r, v) => apiClient.post(`${BREACHES}/${r.id}/aware-at`, { aware_at: v.aware_at, reason: v.reason }, { version: r.row_version }).then(() => undefined),
        },
      ]}
    />
  );
};

// ---------------------------------------------------------------- richieste degli interessati (§27)

const REQUESTS = '/api/privacypa/requests';

export const RequestsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const open = (r: any) => ['RECEIVED', 'IN_PROGRESS'].includes(r.status);
  return (
    <EntityCrudPage
      page="requests" icon={<PersonSearchIcon color="primary" />} endpoint={REQUESTS} perm="dsr" allowDelete annotate="data_subject_request"
      canEditRow={(r) => r.status !== 'CLOSED'}
      fields={[
        { name: 'organizational_unit_id', type: 'unit', required: true },
        { name: 'request_type', type: 'select', options: ['ACCESS', 'RECTIFICATION', 'ERASURE', 'RESTRICTION', 'OBJECTION', 'PORTABILITY', 'OTHER'], enumName: 'requestType', required: true },
        { name: 'received_at', type: 'datetime', required: true },
        { name: 'channel', type: 'select', options: ['EMAIL', 'PEC', 'POSTAL', 'WEB_FORM', 'PHONE', 'IN_PERSON', 'OTHER'], enumName: 'channel', required: true },
        { name: 'identity_verified', type: 'boolean' }, { name: 'identity_verification_notes', type: 'textarea' },
        { name: 'requester_reference', type: 'text', hint: 'wf.hints.requester_reference' },
      ]}
      columns={[
        { key: 'code', render: (r) => r.code },
        { key: 'request_type', render: (r) => <StatusChip enumName="requestType" value={r.request_type} /> },
        { key: 'unit', render: (r, u) => unitName(u, r.organizational_unit_id) },
        { key: 'received_at', render: (r) => stamp(r.received_at) },
        { key: 'due_at', render: (r) => (
          <span style={{ color: open(r) && isPast(r.due_at) ? '#ef4444' : undefined }}>
            {day(r.due_at)}{r.extension_applied && <Chip size="small" variant="outlined" sx={{ ml: 0.5 }} label={t('wf.extended')} />}
          </span>
        ) },
        { key: 'identity', render: (r) => (r.identity_verified ? t('wf.tristate.yes') : t('wf.tristate.no')) },
        { key: 'status', render: (r) => <StatusChip enumName="requestStatus" value={r.status} color={r.status === 'CLOSED' ? 'success' : 'default'} /> },
      ]}
      actions={[
        { key: 'start', permission: 'dsr.edit', visible: (r) => r.status === 'RECEIVED', run: setStatus(REQUESTS, 'IN_PROGRESS') },
        { key: 'tasks', permission: 'dsr.edit', visible: open, fields: [{ name: 'unit_ids', type: 'units', required: true }], notes: 'optional',
          run: (r, v) => apiClient.post(`${REQUESTS}/${r.id}/tasks`, { unit_ids: v.unit_ids, notes: v.notes }).then(() => undefined) },
        { key: 'extension', permission: 'dsr.approve', visible: (r) => open(r) && !r.extension_applied,
          fields: [{ name: 'reason', type: 'textarea', required: true }, { name: 'communicated_at', type: 'datetime' }],
          run: (r, v) => apiClient.post(`${REQUESTS}/${r.id}/extension`, { reason: v.reason, communicated_at: v.communicated_at }, { version: r.row_version }).then(() => undefined) },
        { key: 'respond', permission: 'dsr.approve', color: 'success', visible: (r) => r.status === 'IN_PROGRESS',
          fields: [{ name: 'outcome', type: 'textarea', required: true }, { name: 'response_sent_at', type: 'datetime' }],
          run: (r, v) => apiClient.post(`${REQUESTS}/${r.id}/status`, { status: 'RESPONDED', outcome: v.outcome, response_sent_at: v.response_sent_at }, { version: r.row_version }).then(() => undefined) },
        { key: 'close', permission: 'dsr.approve', visible: (r) => r.status === 'RESPONDED', run: setStatus(REQUESTS, 'CLOSED') },
      ]}
    />
  );
};

// ---------------------------------------------------------------- audit strutturati (§28)

const AUDITS = '/api/privacypa/audits';
const FINDING_FIELDS: FieldSpec[] = [
  { name: 'severity', type: 'select', options: ['HIGH', 'MEDIUM', 'LOW'], enumName: 'severity', required: true },
  { name: 'title', type: 'text', required: true }, { name: 'description', type: 'textarea', required: true }, { name: 'evidence', type: 'textarea' },
];
const FROM_FINDING_FIELDS: FieldSpec[] = [
  { name: 'owner_user_id', type: 'person', required: true }, { name: 'due_date', type: 'date', required: true },
  { name: 'priority', type: 'select', options: ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'], enumName: 'priority', required: true },
];

const AuditDetail: React.FC<{ audit: any; reload: () => Promise<void>; units: { id: string; name: string }[] }> = ({ audit, reload, units }) => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [detail, setDetail] = useState<any>(audit);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ kind: 'finding' | 'action'; finding?: any } | null>(null);
  const [values, setValues] = useState<Values>({});
  useEffect(() => { apiClient.get(`${AUDITS}/${audit.id}`).then(setDetail).catch((e) => setError(e.message)); }, [audit]);

  const canWrite = can('auditplan.edit') && ['PLANNED', 'IN_PROGRESS'].includes(audit.status);
  const fields = dialog?.kind === 'finding' ? FINDING_FIELDS : FROM_FINDING_FIELDS;
  const submit = async () => {
    try {
      const body = bodyFromValues(fields, values);
      if (dialog?.kind === 'finding') await apiClient.post(`${AUDITS}/${audit.id}/findings`, body);
      else await apiClient.post(`${AUDITS}/${audit.id}/findings/${dialog?.finding.id}/corrective-action`, body);
      setDialog(null);
      await reload();
    } catch (e: any) { setError(e.message); }
  };
  const closeFinding = async (f: any) => {
    try { await apiClient.patch(`${AUDITS}/${audit.id}/findings/${f.id}`, { status: 'CLOSED' }); await reload(); } catch (e: any) { setError(e.message); }
  };

  return (
    <>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <Typography variant="body2">{t('wf.fields.planned_date')}: {day(detail.planned_date)} · {t('wf.fields.lead_auditor_user_id')}: {detail.lead_auditor_user_id} · {unitName(units, detail.organizational_unit_id)}</Typography>
      {detail.summary && <Typography variant="body2" color="text.secondary">{detail.summary}</Typography>}
      <Box sx={{ display: 'flex', alignItems: 'center' }}>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>{t('wf.audit.findings')}</Typography>
        {canWrite && <Button size="small" onClick={() => { setValues(emptyFor(FINDING_FIELDS)); setDialog({ kind: 'finding' }); }}>{t('wf.audit.add_finding')}</Button>}
      </Box>
      {(detail.findings || []).length === 0 && <Typography variant="body2" color="text.secondary">{t('wf.audit.no_findings')}</Typography>}
      {(detail.findings || []).map((f: any) => (
        <Box key={f.id} sx={{ borderTop: 1, borderColor: 'divider', py: 0.75 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <StatusChip enumName="severity" value={f.severity} color={f.severity === 'HIGH' ? 'error' : f.severity === 'MEDIUM' ? 'warning' : 'default'} />
            <Typography variant="body2" sx={{ flex: 1 }}><strong>{f.title}</strong></Typography>
            <StatusChip enumName="findingStatus" value={f.status} />
            {can('auditplan.edit') && f.status !== 'CLOSED' && <Button size="small" onClick={() => closeFinding(f)}>{t('wf.audit.close_finding')}</Button>}
            {can('action.create') && can('auditplan.edit') && f.status === 'OPEN' && (
              <Button size="small" onClick={() => { setValues(emptyFor(FROM_FINDING_FIELDS)); setDialog({ kind: 'action', finding: f }); }}>{t('wf.audit.create_action')}</Button>
            )}
          </Box>
          <Typography variant="caption" color="text.secondary" display="block">{f.description}{f.evidence ? ` — ${f.evidence}` : ''}</Typography>
        </Box>
      ))}
      <Dialog open={Boolean(dialog)} onClose={() => setDialog(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{dialog?.kind === 'finding' ? t('wf.audit.add_finding') : t('wf.audit.create_action')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <FormFields fields={fields} values={values} onChange={setValues} units={units} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialog(null)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={submit} disabled={missingRequired(fields, values)}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export const AuditsPage: React.FC = () => (
  <EntityCrudPage
    page="audits" icon={<FactCheckIcon color="primary" />} endpoint={AUDITS} perm="auditplan" allowDelete annotate="audit"
    canEditRow={(r) => ['PLANNED', 'IN_PROGRESS'].includes(r.status)}
    fields={[
      { name: 'title', type: 'text', required: true },
      { name: 'audit_type', type: 'select', options: ['INTERNAL', 'EXTERNAL', 'PROCESSOR', 'SECURITY', 'DPO_REVIEW'], enumName: 'auditType', required: true },
      { name: 'organizational_unit_id', type: 'unit' }, { name: 'planned_date', type: 'date', required: true },
      { name: 'lead_auditor_user_id', type: 'person', required: true },
    ]}
    columns={[
      { key: 'code', render: (r) => r.code }, { key: 'title', render: (r) => r.title },
      { key: 'audit_type', render: (r) => <StatusChip enumName="auditType" value={r.audit_type} /> },
      { key: 'unit', render: (r, u) => unitName(u, r.organizational_unit_id) },
      { key: 'planned_date', render: (r) => day(r.planned_date) },
      { key: 'findings', render: (r) => `${r.open_findings}/${r.findings_count}` },
      { key: 'status', render: (r) => <StatusChip enumName="auditStatus" value={r.status} color={r.status === 'COMPLETED' ? 'success' : 'default'} /> },
    ]}
    detail={(row, reload, units) => <AuditDetail audit={row} reload={reload} units={units} />}
    actions={[
      { key: 'start', permission: 'auditplan.edit', visible: (r) => r.status === 'PLANNED', run: setStatus(AUDITS, 'IN_PROGRESS') },
      { key: 'complete', permission: 'auditplan.edit', color: 'success', visible: (r) => r.status === 'IN_PROGRESS',
        fields: [{ name: 'performed_date', type: 'date', required: true }, { name: 'summary', type: 'textarea', required: true }],
        run: (r, v) => apiClient.post(`${AUDITS}/${r.id}/status`, { status: 'COMPLETED', performed_date: v.performed_date, summary: v.summary }, { version: r.row_version }).then(() => undefined) },
      { key: 'cancel', permission: 'auditplan.edit', color: 'warning', visible: (r) => ['PLANNED', 'IN_PROGRESS'].includes(r.status), run: setStatus(AUDITS, 'CANCELLED') },
    ]}
  />
);

// postAction è riesportata per eventuali pagine che vogliano azioni POST semplici con If-Match.
export { postAction };
