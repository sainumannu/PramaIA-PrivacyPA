import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Card, CardContent, Chip, Container, FormControl, InputLabel, MenuItem, Paper, Select, Table, TableBody,
  TableCell, TableRow, TextField, Typography,
} from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';
import RateReviewIcon from '@mui/icons-material/RateReview';
import SupervisorAccountIcon from '@mui/icons-material/SupervisorAccount';
import AssessmentIcon from '@mui/icons-material/Assessment';
import { PramaLayout } from '../components/PramaLayout';
import { EntityCrudPage, StatusChip } from '../components/EntityCrud';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';

const API = '/api/privacypa';
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : '—');
const stamp = (iso: string | null) => (iso ? iso.replace('T', ' ').slice(0, 16) : '—');

// ---------------------------------------------------------------- pareri del DPO (§43)

const OP = `${API}/dpo/opinions`;
const ENTITY_ENDPOINTS: Record<string, string> = {
  processing_activity: `${API}/processing-activities`, dpia: `${API}/dpias`, privacy_notice: `${API}/notices`, data_breach: `${API}/breaches`,
};

export const OpinionsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  return (
    <EntityCrudPage
      page="opinions" icon={<RateReviewIcon color="primary" />} endpoint={OP} perm="opinion"
      canEditRow={() => false}
      fields={[
        { name: 'entity_type', type: 'select', options: Object.keys(ENTITY_ENDPOINTS), enumName: 'opinionEntity', required: true },
        { name: 'entity_id', type: 'ref', required: true, dependsOn: 'entity_type',
          refEndpoint: (v) => ENTITY_ENDPOINTS[v.entity_type] || null, refLabel: (r) => [r.code, r.title].filter(Boolean).join(' — ') || r.id },
        { name: 'notes', type: 'textarea', hint: 'wf.hints.opinion_notes' },
        { name: 'dpo_user_id', type: 'text' },
      ]}
      columns={[
        { key: 'entity', render: (r) => [r.entity_code, r.entity_title].filter(Boolean).join(' — ') || r.entity_id },
        { key: 'entity_type', render: (r) => <StatusChip enumName="opinionEntity" value={r.entity_type} /> },
        { key: 'status', render: (r) => <StatusChip enumName="opinionStatus" value={r.status} color={r.status === 'ISSUED' ? 'success' : 'default'} /> },
        { key: 'requested_at', render: (r) => stamp(r.requested_at) },
        { key: 'issued_at', render: (r) => stamp(r.issued_at) },
      ]}
      detail={(row) => (
        <>
          <Typography variant="subtitle2">{[row.entity_code, row.entity_title].filter(Boolean).join(' — ')}</Typography>
          <Typography variant="caption" color="text.secondary">{t('opinions.separate_note')}</Typography>
          <Typography variant="subtitle2" sx={{ mt: 1 }}>{t('opinions.request_notes')}</Typography>
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{row.request_notes || '—'}</Typography>
          <Typography variant="subtitle2" sx={{ mt: 1 }}>{t('opinions.opinion')}</Typography>
          <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{row.opinion_text || t('opinions.not_issued')}</Typography>
        </>
      )}
      actions={[
        { key: 'issue', permission: 'opinion.approve', color: 'success', visible: (r) => r.status === 'REQUESTED', notes: 'required',
          run: (r, v) => apiClient.post(`${OP}/${r.id}/issue`, { text: v.notes }, { version: r.row_version }).then(() => undefined) },
        { key: 'withdraw', permission: 'opinion.edit', color: 'warning', visible: (r) => r.status === 'REQUESTED',
          run: (r) => apiClient.post(`${OP}/${r.id}/withdraw`, {}, { version: r.row_version }).then(() => undefined) },
      ]}
    />
  );
};

// ---------------------------------------------------------------- workspace (§43)

interface WorkspaceCard {
  key: string;
  to: string;
  items: { id: string; primary: string; secondary?: string }[];
}

export const DpoWorkspacePage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [ws, setWs] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiClient.get<any>(`${API}/dpo/workspace`).then(setWs).catch((err) => setError(err.message));
  }, []);

  const cards: WorkspaceCard[] = ws ? [
    { key: 'processing_in_review', to: '/approvazioni', items: ws.processing_in_review.map((p: any) => ({ id: p.id, primary: `${p.code} — ${p.title}` })) },
    { key: 'dpias_to_review', to: '/dpia', items: ws.dpias_to_review.map((d: any) => ({ id: d.id, primary: `${d.code} — ${d.title}`, secondary: d.status })) },
    { key: 'opinions_requested', to: '/pareri-dpo', items: ws.opinions_requested.map((o: any) => ({
      id: o.id, primary: [o.code, o.title].filter(Boolean).join(' — '), secondary: `${day(o.requested_at)}${o.assigned_to_me ? ` · ${t('workspace.assigned_to_me')}` : ''}` })) },
    { key: 'breaches_open', to: '/violazioni', items: ws.breaches_open.map((b: any) => ({
      id: b.id, primary: `${b.code} — ${b.title}`, secondary: `${b.hours_since_aware} ${t('wf.hours')}${b.notify_authority === null ? ` · ${t('workspace.no_decision')}` : ''}` })) },
    { key: 'audits', to: '/audit-programmati', items: ws.audits.map((a: any) => ({ id: a.id, primary: `${a.code} — ${a.title}`, secondary: day(a.planned_date) })) },
    { key: 'recommended_actions', to: '/azioni', items: ws.recommended_actions.map((a: any) => ({ id: a.id, primary: `${a.code} — ${a.title}`, secondary: day(a.due_date) })) },
  ] : [];

  const followups: [string, number][] = ws ? Object.entries(ws.followups) as [string, number][] : [];

  return (
    <PramaLayout pageTitle={t('workspace.title')}>
      <Container maxWidth="xl">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <SupervisorAccountIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('workspace.title')}</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('workspace.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {ws && (
          <>
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 2 }}>
              {followups.map(([key, count]) => (
                <Chip key={key} color={count > 0 && key !== 'opinions_pending' ? 'warning' : 'default'} label={`${t(`workspace.followups.${key}`)}: ${count}`} />
              ))}
              <Chip component={RouterLink} to="/relazione-annuale" clickable color="primary" label={`${t('workspace.annual_report')} ${ws.annual_report.year}: ${ws.annual_report.status ? t(`report.status.${ws.annual_report.status}`) : t('workspace.not_started')}`} />
            </Box>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr', xl: '1fr 1fr 1fr' }, gap: 2 }}>
              {cards.map((card) => (
                <Card key={card.key} variant="outlined">
                  <CardContent>
                    <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                      <Typography variant="h6" sx={{ flex: 1 }}>{t(`workspace.cards.${card.key}`)}</Typography>
                      <Button size="small" component={RouterLink} to={card.to}>{t('wf.open')}</Button>
                    </Box>
                    {card.items.length === 0 && <Typography variant="body2" color="text.secondary">{t('workspace.nothing')}</Typography>}
                    {card.items.map((item) => (
                      <Box key={item.id} sx={{ py: 0.5 }}>
                        <Typography variant="body2">{item.primary}</Typography>
                        {item.secondary && <Typography variant="caption" color="text.secondary">{item.secondary}</Typography>}
                      </Box>
                    ))}
                  </CardContent>
                </Card>
              ))}
              <Card variant="outlined">
                <CardContent>
                  <Typography variant="h6" sx={{ mb: 1 }}>{t('workspace.cards.calendar')}</Typography>
                  {ws.calendar.length === 0 && <Typography variant="body2" color="text.secondary">{t('workspace.nothing')}</Typography>}
                  {ws.calendar.map((c: any) => (
                    <Box key={`${c.kind}-${c.entity_id}`} sx={{ py: 0.5 }}>
                      <Typography variant="body2">{day(c.date)} — {t(`workspace.kinds.${c.kind}`)}</Typography>
                      <Typography variant="caption" color="text.secondary">{c.code}</Typography>
                    </Box>
                  ))}
                </CardContent>
              </Card>
            </Box>
          </>
        )}
      </Container>
    </PramaLayout>
  );
};

// ---------------------------------------------------------------- relazione annuale (§42)

const RP = `${API}/reports/annual`;
const SECTIONS = ['registry', 'reviews', 'dpia', 'requests', 'breaches', 'audits', 'suppliers', 'training', 'actions', 'criticalities', 'dpo_activity', 'next_year_plan'];

const plain = (value: any): string => {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return Object.entries(value).map(([k, v]) => `${k}: ${v}`).join(', ') || '—';
  return String(value);
};

export const AnnualReportPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const [report, setReport] = useState<any | null>(null);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const apply = (view: any) => {
    setReport(view);
    setComments(view.comments || {});
  };
  const load = () => {
    setError(null);
    apiClient.get<any>(`${RP}/${year}`).then(apply).catch((err) => { setReport(null); setError(err.message); });
  };
  useEffect(load, [year]);

  const run = (call: () => Promise<any>, done?: string) => call().then((view) => { apply(view); if (done) setInfo(done); }).catch((err) => setError(err.message));
  const final = report?.status === 'FINAL';
  const version = report?.row_version ?? 0;

  return (
    <PramaLayout pageTitle={t('report.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
          <AssessmentIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('report.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          <FormControl size="small" sx={{ minWidth: 110 }}>
            <InputLabel>{t('report.year')}</InputLabel>
            <Select label={t('report.year')} value={year} onChange={(e) => setYear(Number(e.target.value))}>
              {[thisYear + 1, thisYear, thisYear - 1, thisYear - 2, thisYear - 3].map((y) => <MenuItem key={y} value={y}>{y}</MenuItem>)}
            </Select>
          </FormControl>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('report.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo(null)}>{info}</Alert>}

        {report && (
          <>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', mb: 2, flexWrap: 'wrap' }}>
              <Chip color={final ? 'success' : 'warning'} label={t(`report.status.${report.status}`)} />
              {report.watermark && <Chip variant="outlined" label={t('report.watermark')} />}
              {final && <Typography variant="caption" color="text.secondary">{t('report.finalized_by', { user: report.finalized_by, date: day(report.finalized_at) })}</Typography>}
              <Box sx={{ flex: 1 }} />
              <Button onClick={() => apiClient.download(`${RP}/${year}/export`, `relazione-annuale-${year}.docx`).catch((err) => setError(err.message))}>{t('report.export')}</Button>
              {!final && can('report.edit') && (
                <Button variant="outlined" onClick={() => run(() => apiClient.put(`${RP}/${year}/comments`, { comments }, { version }), t('report.saved'))}>{t('report.save_comments')}</Button>
              )}
              {!final && can('report.approve') && (
                <Button variant="contained" color="success" onClick={() => {
                  if (window.confirm(t('report.confirm_finalize'))) run(() => apiClient.post(`${RP}/${year}/finalize`, {}, { version }), t('report.finalized'));
                }}>{t('report.finalize')}</Button>
              )}
              {final && can('report.reopen') && (
                <Button variant="outlined" color="warning" onClick={() => run(() => apiClient.post(`${RP}/${year}/reopen`, {}, { version }), t('report.reopened'))}>{t('report.reopen')}</Button>
              )}
            </Box>

            {SECTIONS.map((key, index) => {
              const data = report.data[key];
              return (
                <Paper key={key} sx={{ p: 2, mb: 2 }}>
                  <Typography variant="h6">{index + 1}. {t(`report.sections.${key}`)}</Typography>
                  {Array.isArray(data) ? (
                    data.length === 0 ? <Typography variant="body2" color="text.secondary">{t('report.no_criticalities')}</Typography> : (
                      <Table size="small">
                        <TableBody>
                          {data.map((c: any) => (
                            <TableRow key={`${c.rule_code}-${c.severity}`}>
                              <TableCell>{c.rule_code}</TableCell>
                              <TableCell><StatusChip enumName="severity" value={c.severity} color={c.severity === 'HIGH' ? 'error' : c.severity === 'MEDIUM' ? 'warning' : 'default'} /></TableCell>
                              <TableCell align="right">{c.count}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    )
                  ) : (
                    <Table size="small">
                      <TableBody>
                        {Object.entries(data || {}).map(([name, value]) => (
                          <TableRow key={name}>
                            <TableCell sx={{ width: '55%' }}>{t(`report.labels.${name}`)}</TableCell>
                            <TableCell>{plain(value)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                  <TextField
                    sx={{ mt: 1.5 }} fullWidth multiline minRows={2} label={t('report.comment')} value={comments[key] || ''}
                    disabled={final || !can('report.edit')} inputProps={{ maxLength: 8000 }}
                    onChange={(e) => setComments({ ...comments, [key]: e.target.value })}
                  />
                </Paper>
              );
            })}
            <Paper sx={{ p: 2, mb: 2 }}>
              <Typography variant="h6">{t('report.sections.conclusions')}</Typography>
              <TextField
                sx={{ mt: 1.5 }} fullWidth multiline minRows={3} label={t('report.comment')} value={comments.conclusions || ''}
                disabled={final || !can('report.edit')} inputProps={{ maxLength: 8000 }}
                onChange={(e) => setComments({ ...comments, conclusions: e.target.value })}
              />
            </Paper>
          </>
        )}
      </Container>
    </PramaLayout>
  );
};
