import React, { useEffect, useState } from 'react';
import {
  Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Paper, Switch,
  Table, TableBody, TableCell, TableHead, TableRow, Typography,
} from '@mui/material';
import SchoolIcon from '@mui/icons-material/School';
import VerifiedUserIcon from '@mui/icons-material/VerifiedUser';
import BadgeIcon from '@mui/icons-material/Badge';
import { PramaLayout } from '../components/PramaLayout';
import {
  EntityCrudPage, FieldSpec, FormFields, StatusChip, Values, bodyFromValues, emptyFor, missingRequired, postAction,
} from '../components/EntityCrud';
import { PortalPerson, usePortalPeople } from '../components/PortalPeople';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';

const day = (iso: string | null) => (iso ? iso.slice(0, 10) : '—');
const isPast = (iso: string | null) => Boolean(iso && iso.slice(0, 10) < new Date().toISOString().slice(0, 10));
const ROLES = ['TENANT_ADMIN', 'PRIVACY_MANAGER', 'DPO', 'MANAGER', 'PRIVACY_REFERENT', 'ICT', 'RTD', 'OPERATOR', 'AUDITOR', 'READ_ONLY'];

// ---------------------------------------------------------------- formazione (§44)

const TR = '/api/privacypa/training';
const userList = (text: unknown) => String(text || '').split(/[\s,;]+/).filter(Boolean);

const CourseDetail: React.FC<{ course: any }> = ({ course }) => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [assignments, setAssignments] = useState<any[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState<{ row: any; mode: 'complete' | 'waive' } | null>(null);
  const [values, setValues] = useState<Values>({});

  const load = async () => {
    try {
      setAssignments(await apiClient.get<any[]>(`${TR}/assignments?course_id=${course.id}`));
      setSessions(await apiClient.get<any[]>(`${TR}/courses/${course.id}/sessions`));
    } catch (err: any) {
      setError(err.message);
    }
  };
  useEffect(() => { load(); }, [course.id]);

  const fields: FieldSpec[] = running?.mode === 'complete'
    ? [{ name: 'completed_at', type: 'date', required: true }, { name: 'score', type: 'number' }]
    : [{ name: 'reason', type: 'textarea', required: true }];

  const confirm = async () => {
    if (!running) return;
    const body = bodyFromValues(fields, values);
    try {
      await apiClient.post(`${TR}/assignments/${running.row.id}/${running.mode}`, body, { version: running.row.row_version });
      setRunning(null);
      await load();
    } catch (err: any) {
      setError(err.message);
    }
  };

  return (
    <>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <Typography variant="subtitle2">{t('people.sessions')}</Typography>
      {sessions.length === 0 && <Typography variant="body2" color="text.secondary">{t('wf.empty')}</Typography>}
      {sessions.map((s) => (
        <Typography key={s.id} variant="body2">{day(s.session_date)}{s.trainer ? ` — ${s.trainer}` : ''}{s.location ? ` (${s.location})` : ''}</Typography>
      ))}
      <Typography variant="subtitle2" sx={{ mt: 1 }}>{t('people.assignments')}</Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>{t('wf.columns.user')}</TableCell><TableCell>{t('wf.columns.due_date')}</TableCell>
            <TableCell>{t('wf.columns.status')}</TableCell><TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {assignments.length === 0 && <TableRow><TableCell colSpan={4}>{t('wf.empty')}</TableCell></TableRow>}
          {assignments.map((a) => (
            <TableRow key={a.id}>
              <TableCell>{a.user_id}</TableCell>
              <TableCell sx={{ color: a.status === 'ASSIGNED' && isPast(a.due_date) ? '#ef4444' : undefined }}>{day(a.due_date)}</TableCell>
              <TableCell><StatusChip enumName="assignmentStatus" value={a.status} color={a.status === 'COMPLETED' ? 'success' : 'default'} /></TableCell>
              <TableCell align="right">
                {a.status === 'ASSIGNED' && can('training.edit') && (
                  <>
                    <Button size="small" color="success" onClick={() => { setValues(emptyFor([{ name: 'completed_at', type: 'date' }, { name: 'score', type: 'number' }])); setRunning({ row: a, mode: 'complete' }); }}>{t('wf.actions.complete')}</Button>
                    <Button size="small" color="warning" onClick={() => { setValues(emptyFor([{ name: 'reason', type: 'textarea' }])); setRunning({ row: a, mode: 'waive' }); }}>{t('wf.actions.waive')}</Button>
                  </>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Dialog open={Boolean(running)} onClose={() => setRunning(null)} maxWidth="xs" fullWidth>
        <DialogTitle>{running ? t(`wf.actions.${running.mode}`) : ''}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <FormFields fields={fields} values={values} onChange={setValues} units={[]} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRunning(null)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={confirm} disabled={missingRequired(fields, values)}>{t('wf.confirm')}</Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export const TrainingCoursesPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const hasTargets = (r: any) => (r.target_roles || []).length > 0 || (r.target_unit_ids || []).length > 0;
  return (
    <EntityCrudPage
      page="training" icon={<SchoolIcon color="primary" />} endpoint={`${TR}/courses`} perm="training"
      fields={[
        { name: 'title', type: 'text', required: true }, { name: 'version', type: 'text', required: true },
        { name: 'description', type: 'textarea' }, { name: 'mandatory', type: 'boolean' },
        { name: 'validity_months', type: 'number', hint: 'wf.hints.validity_months' },
        { name: 'target_roles', type: 'multiselect', options: ROLES, enumName: 'role' },
        { name: 'target_unit_ids', type: 'units' },
      ]}
      columns={[
        { key: 'code', render: (r) => r.code }, { key: 'title', render: (r) => r.title }, { key: 'version', render: (r) => r.version },
        { key: 'mandatory', render: (r) => (r.mandatory ? <Chip size="small" color="warning" label={t('wf.tristate.yes')} /> : t('wf.tristate.no')) },
        { key: 'validity', render: (r) => (r.validity_months ? `${r.validity_months} ${t('people.months')}` : '—') },
        { key: 'active', render: (r) => <Chip size="small" color={r.active ? 'success' : 'default'} label={t(r.active ? 'people.active' : 'people.retired')} /> },
      ]}
      detail={(row) => <CourseDetail course={row} />}
      actions={[
        { key: 'assign', permission: 'training.edit', visible: (r) => r.active,
          fields: [{ name: 'user_ids', type: 'people', required: true, hint: 'wf.hints.user_ids' }, { name: 'due_date', type: 'date', required: true }],
          run: (r, v) => apiClient.post(`${TR}/courses/${r.id}/assign`, { user_ids: userList(v.user_ids), due_date: v.due_date }).then(() => undefined) },
        { key: 'assign_targets', permission: 'training.edit', visible: (r) => r.active && hasTargets(r),
          fields: [{ name: 'due_date', type: 'date', required: true }],
          run: (r, v) => apiClient.post(`${TR}/courses/${r.id}/assign-targets`, { due_date: v.due_date }).then(() => undefined) },
        { key: 'add_session', permission: 'training.edit',
          fields: [{ name: 'session_date', type: 'date', required: true }, { name: 'trainer', type: 'text' }, { name: 'location', type: 'text' }],
          run: (r, v) => apiClient.post(`${TR}/courses/${r.id}/sessions`, { session_date: v.session_date, trainer: v.trainer, location: v.location }).then(() => undefined) },
        { key: 'retire', permission: 'training.edit', color: 'warning', visible: (r) => r.active,
          run: (r) => apiClient.post(`${TR}/courses/${r.id}/active`, { active: false }, { version: r.row_version }).then(() => undefined) },
        { key: 'activate', permission: 'training.edit', visible: (r) => !r.active,
          run: (r) => apiClient.post(`${TR}/courses/${r.id}/active`, { active: true }, { version: r.row_version }).then(() => undefined) },
      ]}
    />
  );
};

// Assegnazioni: chi gestisce la formazione vede tutte, gli altri solo le proprie (lo decide il server).
export const TrainingAssignmentsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [rows, setRows] = useState<any[]>([]);
  const [courses, setCourses] = useState<Record<string, any>>({});
  const [mine, setMine] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiClient.get<any[]>(`${TR}/courses`).then((list) => setCourses(Object.fromEntries(list.map((c) => [c.id, c])))).catch(() => undefined);
  }, []);
  useEffect(() => {
    apiClient.get<any[]>(`${TR}/assignments${mine ? '?mine=true' : ''}`).then(setRows).catch((err) => setError(err.message));
  }, [mine]);

  return (
    <PramaLayout pageTitle={t('wf.pages.trainingAssignments.title')}>
      <Container maxWidth="xl">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <BadgeIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('wf.pages.trainingAssignments.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          <FormControlLabel control={<Switch checked={mine} onChange={(e) => setMine(e.target.checked)} />} label={t('people.only_mine')} />
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('wf.pages.trainingAssignments.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <Paper sx={{ overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                {['course', 'user', 'due_date', 'status'].map((k) => <TableCell key={k}>{t(`wf.columns.${k}`)}</TableCell>)}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.length === 0 && <TableRow><TableCell colSpan={4}><Typography variant="body2" color="text.secondary">{t('wf.empty')}</Typography></TableCell></TableRow>}
              {rows.map((a) => (
                <TableRow key={a.id} hover>
                  <TableCell>{courses[a.course_id] ? `${courses[a.course_id].code} — ${courses[a.course_id].title}` : a.course_id}</TableCell>
                  <TableCell>{a.user_id}</TableCell>
                  <TableCell sx={{ color: a.status === 'ASSIGNED' && isPast(a.due_date) ? '#ef4444' : undefined }}>{day(a.due_date)}</TableCell>
                  <TableCell><StatusChip enumName="assignmentStatus" value={a.status} color={a.status === 'COMPLETED' ? 'success' : 'default'} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>
    </PramaLayout>
  );
};

/// ---------------------------------------------------------------- autorizzati al trattamento (§45)

const AU = '/api/privacypa/authorizations';
const API = '/api/privacypa';
const OPERATIONS = ['READ', 'CREATE', 'UPDATE', 'DELETE', 'EXPORT', 'PRINT', 'DOWNLOAD', 'ADMINISTER'];
const SCOPE_TYPES = ['ALL_RECORDS', 'ASSIGNED_CASES', 'ORGANIZATIONAL_UNIT', 'GEOGRAPHIC_AREA', 'SUBJECT_SET', 'CUSTOM'];
const PROFILE_ADMIN = { create: 'authorization.administer', edit: 'authorization.administer', delete: 'authorization.administer' };

const useNames = () => {
  const [names, setNames] = useState<Record<string, string>>({});
  useEffect(() => {
    const load = (path: string, label: (r: any) => string) => apiClient.get<any[]>(path).then((rows) => rows.map((r) => [r.id, label(r)] as [string, string])).catch(() => []);
    Promise.all([
      load(`${API}/processing-activities`, (r) => `${r.code} — ${r.title}`), load(`${API}/assets`, (r) => r.name),
      load(`${AU}/profiles`, (r) => (r.code ? `${r.code} — ${r.name}` : r.name)),
    ]).then((lists) => setNames(Object.fromEntries(lists.flat())));
  }, []);
  return names;
};

const Line: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <Box sx={{ display: 'flex', gap: 2 }}>
    <Typography variant="body2" color="text.secondary" sx={{ minWidth: 220 }}>{label}</Typography>
    <Typography variant="body2" component="div">{children || '—'}</Typography>
  </Box>
);

// Estremi della concessione e origine (richieste di accesso dall'HelpDesk).
const AuthorizationDetail: React.FC<{ row: any; names: Record<string, string>; person?: PortalPerson }> = ({ row, names, person }) => {
  const { t } = useTranslation('privacypa');
  const ops = (row.operations || []).map((o: string) => t(`wf.enum.accessOperation.${o}`)).join(', ');
  return (
    <>
      <Line label={t('wf.fields.user_id')}>
        {person ? `${person.name}${person.email ? ` (${person.email})` : ''}${person.units.length ? ` — ${person.units.join(' · ')}` : ''}` : row.user_id}
      </Line>
      <Line label={t('wf.fields.authorization_profile_id')}>
        {names[row.authorization_profile_id] || row.authorization_profile_id}
        {row.profile_version ? ` (${t('people.profile_version', { version: row.profile_version })})` : ''}
      </Line>
      <Line label={t('wf.fields.operations')}>{ops}</Line>
      <Line label={t('wf.fields.scope')}>{row.scope ? `${t(`wf.enum.accessScope.${row.scope.type}`)}${row.scope.value ? ` — ${row.scope.value}` : ''}` : ''}</Line>
      <Line label={t('wf.columns.period')}>{`${day(row.valid_from)} → ${day(row.valid_to)}`}</Line>
      <Typography variant="subtitle2" sx={{ mt: 1 }}>{t('people.authorization_details')}</Typography>
      <Line label={t('wf.fields.authorized_by_name')}>{row.authorized_by_name}</Line>
      <Line label={t('wf.fields.authorization_reference')}>{row.authorization_reference}</Line>
      <Line label={t('wf.fields.authorization_date')}>{row.authorization_date ? day(row.authorization_date) : ''}</Line>
      <Line label={t('wf.fields.authorization_notes')}>{row.authorization_notes}</Line>
      <Line label={t('wf.fields.source_ref')}>
        {row.source_ref ? `${row.source_module || ''} ${row.source_ref}` : ''}
        {row.forced && <Chip size="small" color="warning" label={t('people.forced')} sx={{ ml: 1 }} />}
      </Line>
      {row.revoked_at && (
        <>
          <Typography variant="subtitle2" sx={{ mt: 1 }}>{t('people.auth_state.revoked')}</Typography>
          <Line label={t('wf.fields.reason')}>{row.revoked_reason}</Line>
          <Line label={t('wf.fields.revocation_reference')}>{row.revocation_reference}</Line>
        </>
      )}
    </>
  );
};

export const AuthorizationsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const names = useNames();
  const people = usePortalPeople();
  const state = (r: any) => (r.revoked_at ? 'revoked' : r.active ? 'active' : 'expired');

  return (
    <EntityCrudPage
      page="authorizations" icon={<VerifiedUserIcon color="primary" />} endpoint={AU} perm="authorization"
      canEditRow={(r) => !r.revoked_at}
      fields={[
        { name: 'user_id', type: 'person', required: true, createOnly: true, hint: 'people.person.hint' },
        { name: 'processing_activity_id', type: 'ref', refEndpoint: `${API}/processing-activities`, refLabel: (r) => `${r.code} — ${r.title}` },
        { name: 'asset_id', type: 'ref', refEndpoint: `${API}/assets`, refLabel: (r) => r.name },
        { name: 'authorization_profile_id', type: 'ref', refEndpoint: `${AU}/profiles`, required: true, refLabel: (r) => (r.code ? `${r.code} — ${r.name}` : r.name) },
        { name: 'operations', type: 'multiselect', options: OPERATIONS, enumName: 'accessOperation' },
        { name: 'valid_from', type: 'date', required: true }, { name: 'valid_to', type: 'date' },
        { name: 'authorized_by_name', type: 'text', hint: 'people.hints.authorized_by' },
        { name: 'authorization_reference', type: 'text' }, { name: 'authorization_date', type: 'date' },
        { name: 'authorization_notes', type: 'textarea' },
      ]}
      toBody={(body, editing) => (editing ? { ...body, user_id: editing.user_id } : body)}
      columns={[
        { key: 'user', render: (r) => people.nameOf(r.user_id) },
        { key: 'subject', render: (r) => names[r.processing_activity_id] || names[r.asset_id] || '—' },
        { key: 'profile', render: (r) => names[r.authorization_profile_id] || '—' },
        { key: 'period', render: (r) => `${day(r.valid_from)} → ${day(r.valid_to)}` },
        { key: 'status', render: (r) => <Chip size="small" color={state(r) === 'active' ? 'success' : state(r) === 'expired' ? 'warning' : 'default'} label={t(`people.auth_state.${state(r)}`)} /> },
        { key: 'source_ref', render: (r) => (r.source_ref ? <>{r.source_ref}{r.forced && <Chip size="small" color="warning" label={t('people.forced')} sx={{ ml: 0.5 }} />}</> : '—') },
        { key: 'reviewed', render: (r) => day(r.last_reviewed_at) },
      ]}
      detail={(r) => <AuthorizationDetail row={r} names={names} person={people.byId[r.user_id]} />}
      actions={[
        { key: 'confirm_review', permission: 'authorization.edit', visible: (r) => !r.revoked_at, run: postAction(AU, '/confirm-review') },
        { key: 'revoke', permission: 'authorization.edit', color: 'error', visible: (r) => !r.revoked_at, notes: 'required',
          fields: [{ name: 'revocation_reference', type: 'text' }],
          run: (r, v) => apiClient.post(`${AU}/${r.id}/revoke`, { reason: v.notes, revocation_reference: v.revocation_reference }, { version: r.row_version }).then(() => undefined) },
      ]}
    />
  );
};

// Profili di accesso: sistema, operazioni e ambiti ammessi, requisiti. L'HelpDesk li usa per le richieste di accesso.
export const AuthorizationProfilesPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const names = useNames();
  return (
    <EntityCrudPage
      page="authProfiles" icon={<VerifiedUserIcon color="primary" />} endpoint={`${AU}/profiles`} perm="authorization"
      permissions={PROFILE_ADMIN} allowDelete
      fields={[
        { name: 'code', type: 'text', hint: 'people.hints.profile_code' }, { name: 'name', type: 'text', required: true },
        { name: 'description', type: 'textarea' },
        { name: 'asset_id', type: 'ref', refEndpoint: `${API}/assets`, refLabel: (r) => r.name },
        { name: 'allowed_operations', type: 'multiselect', options: OPERATIONS, enumName: 'accessOperation' },
        { name: 'allowed_scope_types', type: 'multiselect', options: SCOPE_TYPES, enumName: 'accessScope' },
        { name: 'data_category_ids', type: 'refs', refEndpoint: `${API}/catalogs/data-categories`, refLabel: (r) => r.name },
        { name: 'processing_activity_ids', type: 'refs', refEndpoint: `${API}/processing-activities`, refLabel: (r) => `${r.code} — ${r.title}` },
        { name: 'required_training_course_ids', type: 'refs', refEndpoint: `${TR}/courses`, refLabel: (r) => `${r.code} — ${r.title}` },
        { name: 'max_duration_days', type: 'number', hint: 'people.hints.max_duration' },
        { name: 'active', type: 'boolean', defaultValue: true },
      ]}
      columns={[
        { key: 'code', render: (r) => r.code || '—' },
        { key: 'name', render: (r) => r.name },
        { key: 'asset', render: (r) => names[r.asset_id] || '—' },
        { key: 'operations', render: (r) => (r.allowed_operations || []).map((o: string) => t(`wf.enum.accessOperation.${o}`)).join(', ') || '—' },
        { key: 'max_duration', render: (r) => (r.max_duration_days ? t('people.days', { days: r.max_duration_days }) : '—') },
        { key: 'active', render: (r) => <Chip size="small" color={r.active ? 'success' : 'default'} label={r.active ? t('people.active') : t('people.retired')} /> },
      ]}
    />
  );
};
