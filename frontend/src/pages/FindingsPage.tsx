import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle, FormControl,
  InputLabel, MenuItem, Paper, Select, Switch, Tab, Table, TableBody, TableCell, TableHead, TableRow,
  Tabs, TextField, Typography,
} from '@mui/material';
import FindInPageIcon from '@mui/icons-material/FindInPage';
import { Link as RouterLink } from 'react-router-dom';
import { PramaLayout } from '../components/PramaLayout';
import StatusNotesDialog from '../components/StatusNotesDialog';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';

type Severity = 'HIGH' | 'MEDIUM' | 'LOW';
type FindingStatus = 'OPEN' | 'ACKNOWLEDGED' | 'ACCEPTED' | 'RESOLVED';

interface Finding {
  id: string;
  row_version: number;
  code: string;
  severity: Severity;
  status: FindingStatus;
  entity_type: string;
  entity_id: string;
  entity_code: string | null;
  message: string;
  due_date: string | null;
  params: Record<string, string>;
  detected_at: string;
  resolution_notes: string | null;
}

interface FindingDetail extends Finding {
  why: {
    rule_name: string | null;
    rule_description: string | null;
    condition: string;
    rule_version: number;
    current_rule_version: number | null;
    rule_changed_since: boolean;
    current_condition: string | null;
  };
  history: { event_type: string; event_time: string; actor: string; details: Record<string, any> | null }[];
}

interface Rule {
  code: string;
  version: number;
  name: string;
  description: string | null;
  severity: Severity;
  enabled: boolean;
  condition: string | null;
  params: Record<string, number>;
}

const ROUTES: Record<string, string> = {
  processing_activity: '/registro',
  contract: '/filiera',
  subprocessor: '/filiera',
  processor_agreement: '/fornitori',
  asset: '/sistemi',
  privacy_notice: '/informative',
  task: '/task',
  document: '/documenti',
  dpia: '/dpia',
};
const COLORS: Record<Severity, 'error' | 'warning' | 'default'> = { HIGH: 'error', MEDIUM: 'warning', LOW: 'default' };
const STATUS_FILTERS = ['DEFAULT', 'OPEN', 'ACKNOWLEDGED', 'ACCEPTED', 'RESOLVED', 'ALL'];

export const FindingsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [tab, setTab] = useState(0);
  const [items, setItems] = useState<Finding[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [severity, setSeverity] = useState('');
  const [statusFilter, setStatusFilter] = useState('DEFAULT');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [detail, setDetail] = useState<FindingDetail | null>(null);
  const [accepting, setAccepting] = useState<Finding | null>(null);
  const [acceptError, setAcceptError] = useState<string | null>(null);

  const loadFindings = useCallback(async () => {
    const params = new URLSearchParams();
    if (severity) params.set('severity', severity);
    if (statusFilter !== 'DEFAULT') params.set('status', statusFilter);
    try {
      setItems(await apiClient.get<Finding[]>(`/api/privacypa/findings${params.toString() ? `?${params}` : ''}`));
    } catch (err: any) {
      setError(err.message);
    }
  }, [severity, statusFilter]);

  const loadRules = async () => {
    try {
      setRules(await apiClient.get<Rule[]>('/api/privacypa/compliance/rules'));
    } catch (err: any) {
      setError(err.message);
    }
  };

  useEffect(() => { loadFindings(); }, [loadFindings]);
  useEffect(() => { if (tab === 1) loadRules(); }, [tab]);

  const recalculate = async () => {
    try {
      const stats = await apiClient.post<{ opened: number; resolved: number }>('/api/privacypa/findings/evaluate', {});
      setInfo(t('findings.evaluated', { opened: String(stats.opened), resolved: String(stats.resolved) }));
      await loadFindings();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const changeStatus = async (finding: Finding, target: FindingStatus, notes?: string) => {
    try {
      await apiClient.post(`/api/privacypa/findings/${finding.id}/status`, { status: target, notes: notes || null }, { version: finding.row_version });
      setAccepting(null);
      setAcceptError(null);
      setInfo(t('findings.changed'));
      await loadFindings();
    } catch (err: any) {
      if (target === 'ACCEPTED') setAcceptError(err.message); else setError(err.message);
      await loadFindings();
    }
  };

  const openDetail = async (finding: Finding) => {
    try {
      setDetail(await apiClient.get<FindingDetail>(`/api/privacypa/findings/${finding.id}`));
    } catch (err: any) {
      setError(err.message);
    }
  };

  const updateRule = async (rule: Rule, patch: Partial<{ enabled: boolean; severity: Severity; params: Record<string, number> }>) => {
    try {
      await apiClient.put(`/api/privacypa/compliance/rules/${rule.code}`, patch, { version: rule.version });
      setInfo(t('findings.rules.saved'));
    } catch (err: any) {
      setError(err.message);
    }
    await loadRules();
  };

  const actionsFor = (f: Finding): { target: FindingStatus; permission: string }[] => {
    if (f.status === 'OPEN') return [{ target: 'ACKNOWLEDGED', permission: 'finding.edit' }, { target: 'ACCEPTED', permission: 'finding.approve' }];
    if (f.status === 'ACKNOWLEDGED') return [{ target: 'ACCEPTED', permission: 'finding.approve' }, { target: 'OPEN', permission: 'finding.edit' }];
    if (f.status === 'ACCEPTED') return [{ target: 'OPEN', permission: 'finding.approve' }];
    return [];
  };

  return (
    <PramaLayout pageTitle={t('findings.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <FindInPageIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('findings.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          {can('finding.edit') && <Button variant="outlined" onClick={recalculate}>{t('findings.recalculate')}</Button>}
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('findings.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo(null)}>{info}</Alert>}

        <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
          <Tab label={t('findings.tabs.findings')} />
          <Tab label={t('findings.tabs.rules')} />
        </Tabs>

        {tab === 0 && (
          <>
            <Box sx={{ display: 'flex', gap: 2, mb: 2 }}>
              <FormControl size="small" sx={{ minWidth: 160 }}>
                <InputLabel>{t('findings.severity')}</InputLabel>
                <Select label={t('findings.severity')} value={severity} onChange={(e) => setSeverity(e.target.value)}>
                  <MenuItem value="">{t('findings.all')}</MenuItem>
                  {['HIGH', 'MEDIUM', 'LOW'].map((s) => <MenuItem key={s} value={s}>{t(`findings.sev.${s}`)}</MenuItem>)}
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ minWidth: 160 }}>
                <InputLabel>{t('findings.status.title')}</InputLabel>
                <Select label={t('findings.status.title')} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                  {STATUS_FILTERS.map((s) => <MenuItem key={s} value={s}>{t(`findings.status.${s}`)}</MenuItem>)}
                </Select>
              </FormControl>
            </Box>
            <Paper>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>{t('findings.severity')}</TableCell>
                    <TableCell>{t('findings.finding')}</TableCell>
                    <TableCell>{t('findings.due')}</TableCell>
                    <TableCell>{t('findings.status.title')}</TableCell>
                    <TableCell />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {items.length === 0 && (
                    <TableRow><TableCell colSpan={5}><Typography variant="body2" color="text.secondary">{t('findings.empty')}</Typography></TableCell></TableRow>
                  )}
                  {items.map((f) => (
                    <TableRow key={f.id} hover>
                      <TableCell><Chip size="small" color={COLORS[f.severity]} label={t(`findings.sev.${f.severity}`)} /></TableCell>
                      <TableCell>
                        {t(`findings.codes.${f.code === 'NOTICE_REVIEW_REQUIRED' && !f.params.processing ? 'NOTICE_REVIEW_CONTROLLER' : f.code}`, f.params) || f.message}
                        {f.resolution_notes && <Typography variant="caption" display="block" color="text.secondary">{f.resolution_notes}</Typography>}
                      </TableCell>
                      <TableCell>{f.due_date || '—'}</TableCell>
                      <TableCell><Chip size="small" variant="outlined" label={t(`findings.status.${f.status}`)} /></TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        <Button size="small" onClick={() => openDetail(f)}>{t('findings.why_button')}</Button>
                        {actionsFor(f).filter((a) => can(a.permission)).map((a) => (
                          <Button
                            key={a.target} size="small"
                            onClick={() => (a.target === 'ACCEPTED' ? (setAcceptError(null), setAccepting(f)) : changeStatus(f, a.target))}
                          >
                            {t(`findings.actions.${a.target}`)}
                          </Button>
                        ))}
                        <Button size="small" component={RouterLink} to={ROUTES[f.entity_type] || '/'}>{t('findings.open')}</Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Paper>
          </>
        )}

        {tab === 1 && (
          <Paper sx={{ p: 2 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('findings.rules.hint')}</Typography>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t('findings.rules.name')}</TableCell>
                  <TableCell>{t('findings.rules.enabled')}</TableCell>
                  <TableCell>{t('findings.rules.severity')}</TableCell>
                  <TableCell>{t('findings.rules.params')}</TableCell>
                  <TableCell>{t('findings.rules.version')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rules.map((r) => (
                  <TableRow key={r.code} hover>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>{r.name}</Typography>
                      <Typography variant="caption" color="text.secondary" display="block">{r.condition}</Typography>
                    </TableCell>
                    <TableCell>
                      <Switch checked={r.enabled} disabled={!can('finding.administer')} onChange={(e) => updateRule(r, { enabled: e.target.checked })} />
                    </TableCell>
                    <TableCell>
                      <Select size="small" value={r.severity} disabled={!can('finding.administer')} onChange={(e) => updateRule(r, { severity: e.target.value as Severity })}>
                        {['HIGH', 'MEDIUM', 'LOW'].map((s) => <MenuItem key={s} value={s}>{t(`findings.sev.${s}`)}</MenuItem>)}
                      </Select>
                    </TableCell>
                    <TableCell>
                      {Object.entries(r.params).map(([key, value]) => (
                        <TextField
                          key={key} size="small" type="number" label={t(`findings.rules.${key}`)} defaultValue={value}
                          disabled={!can('finding.administer')} sx={{ width: 150 }} inputProps={{ min: 1, max: 3650 }}
                          onBlur={(e) => {
                            const next = Number(e.target.value);
                            if (next && next !== value) updateRule(r, { params: { [key]: next } });
                          }}
                        />
                      ))}
                    </TableCell>
                    <TableCell>v{r.version}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Paper>
        )}
      </Container>

      <StatusNotesDialog
        open={Boolean(accepting)}
        title={t('findings.dialog.accept_title')}
        hint={t('findings.dialog.accept_hint')}
        required
        error={acceptError}
        onConfirm={(notes) => accepting && changeStatus(accepting, 'ACCEPTED', notes)}
        onClose={() => { setAccepting(null); setAcceptError(null); }}
      />

      <Dialog open={Boolean(detail)} onClose={() => setDetail(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('findings.why.title')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
          {detail && (
            <>
              <Typography variant="body2"><strong>{t('findings.why.rule')}:</strong> {detail.why.rule_name}</Typography>
              {detail.why.rule_description && <Typography variant="body2" color="text.secondary">{detail.why.rule_description}</Typography>}
              <Typography variant="body2"><strong>{t('findings.why.condition')}:</strong> {detail.why.condition}</Typography>
              <Typography variant="body2"><strong>{t('findings.why.version')}:</strong> {detail.why.rule_version}</Typography>
              {detail.why.rule_changed_since && (
                <Alert severity="info">
                  {t('findings.why.changed', { current: String(detail.why.current_rule_version) })} {detail.why.current_condition}
                </Alert>
              )}
              <Typography variant="body2"><strong>{t('findings.why.detected')}:</strong> {new Date(detail.detected_at).toLocaleString()}</Typography>
              {detail.resolution_notes && <Typography variant="body2"><strong>{t('findings.why.notes')}:</strong> {detail.resolution_notes}</Typography>}
              <Typography variant="subtitle2" sx={{ mt: 1 }}>{t('findings.why.history')}</Typography>
              {detail.history.map((h, i) => (
                <Typography key={i} variant="caption" display="block">
                  {new Date(h.event_time).toLocaleString()} · {h.event_type} · {h.actor === 'system' ? t('findings.why.system') : h.actor}
                </Typography>
              ))}
            </>
          )}
        </DialogContent>
        <DialogActions><Button onClick={() => setDetail(null)}>{t('findings.why.close')}</Button></DialogActions>
      </Dialog>
    </PramaLayout>
  );
};

export default FindingsPage;
