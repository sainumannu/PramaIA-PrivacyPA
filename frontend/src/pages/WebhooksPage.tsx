import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Box, Button, Checkbox, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, FormControlLabel, IconButton,
  InputLabel, ListItemText, MenuItem, Paper, Select, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/DeleteOutline';
import EditIcon from '@mui/icons-material/Edit';
import HistoryIcon from '@mui/icons-material/History';
import KeyIcon from '@mui/icons-material/VpnKey';
import SendIcon from '@mui/icons-material/Send';
import WebhookIcon from '@mui/icons-material/Webhook';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';

const API = '/api/privacypa/webhooks';

interface Subscription {
  id: string; row_version: number; name: string; url: string; events: string[]; active: boolean; disabled_reason: string | null;
  failure_streak: number; last_success_at: string | null; last_failure_at: string | null; deliveries?: Record<string, number>;
}
interface EventDef { name: string; description: string }
interface Delivery {
  id: string; event_name: string; status: string; attempts: number; next_attempt_at: string; last_status_code: number | null;
  last_error: string | null; created_at: string; delivered_at: string | null;
}
interface Form { id?: string; version?: number; name: string; url: string; events: string[]; active: boolean }

const EMPTY: Form = { name: '', url: '', events: [], active: true };
const STATUS_COLOR: Record<string, 'success' | 'warning' | 'error'> = { DELIVERED: 'success', PENDING: 'warning', DEAD: 'error' };
const when = (v: string | null) => (v ? v.replace('T', ' ').slice(0, 16) : '—');

// Endpoint dei webhook (§70): chi riceve gli eventi, con firma HMAC. Il segreto compare una sola volta, alla creazione o alla rotazione.
export const WebhooksPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [rows, setRows] = useState<Subscription[]>([]);
  const [events, setEvents] = useState<EventDef[]>([]);
  const [form, setForm] = useState<Form | null>(null);
  const [secret, setSecret] = useState<{ name: string; value: string } | null>(null);
  const [history, setHistory] = useState<{ sub: Subscription; items: Delivery[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, evs] = await Promise.all([apiClient.get<Subscription[]>(API), apiClient.get<EventDef[]>(`${API}/events`)]);
      setRows(list); setEvents(evs);
    } catch (e: any) { setError(e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const fail = (e: any) => { setInfo(null); setError(e.message); };

  const save = async () => {
    if (!form) return;
    const body = { name: form.name, url: form.url, events: form.events, active: form.active };
    try {
      if (form.id) {
        await apiClient.put(`${API}/${form.id}`, body, { version: form.version });
        setInfo(t('webhooks.saved'));
      } else {
        const created = await apiClient.post<Subscription & { secret: string }>(API, body);
        setSecret({ name: created.name, value: created.secret });
      }
      setForm(null); setError(null); load();
    } catch (e: any) { fail(e); }
  };

  const rotate = async (s: Subscription) => {
    if (!window.confirm(t('webhooks.confirm_rotate'))) return;
    try {
      const r = await apiClient.post<Subscription & { secret: string }>(`${API}/${s.id}/rotate-secret`, {});
      setSecret({ name: r.name, value: r.secret }); setError(null); load();
    } catch (e: any) { fail(e); }
  };
  const remove = async (s: Subscription) => {
    if (!window.confirm(t('webhooks.confirm_delete'))) return;
    try { await apiClient.delete(`${API}/${s.id}`); setInfo(t('webhooks.deleted')); setError(null); load(); } catch (e: any) { fail(e); }
  };
  const test = async (s: Subscription) => {
    try {
      const r = await apiClient.post<Delivery>(`${API}/${s.id}/test`, {});
      if (r.status === 'DELIVERED') { setInfo(t('webhooks.test_ok', { code: r.last_status_code })); setError(null); }
      else { setInfo(null); setError(t('webhooks.test_failed', { reason: r.last_error || r.last_status_code })); }
    } catch (e: any) { fail(e); }
  };
  const openHistory = async (s: Subscription) => {
    try { setHistory({ sub: s, items: await apiClient.get<Delivery[]>(`${API}/${s.id}/deliveries?limit=100`) }); } catch (e: any) { fail(e); }
  };
  const retry = async (d: Delivery) => {
    try {
      await apiClient.post(`${API}/deliveries/${d.id}/retry`, {});
      if (history) setHistory({ ...history, items: await apiClient.get<Delivery[]>(`${API}/${history.sub.id}/deliveries?limit=100`) });
      setInfo(t('webhooks.retry_queued')); setError(null);
    } catch (e: any) { fail(e); }
  };

  return (
    <PramaLayout pageTitle={t('webhooks.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <WebhookIcon color="primary" />
          <Typography variant="h4" fontWeight={600} sx={{ flex: 1 }}>{t('webhooks.title')}</Typography>
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setForm({ ...EMPTY })}>{t('webhooks.add')}</Button>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('webhooks.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo(null)}>{info}</Alert>}

        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('webhooks.name')}</TableCell><TableCell>{t('webhooks.url')}</TableCell><TableCell>{t('webhooks.events')}</TableCell>
                <TableCell>{t('webhooks.state')}</TableCell><TableCell align="right" />
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.length === 0 && <TableRow><TableCell colSpan={5}>{t('webhooks.empty')}</TableCell></TableRow>}
              {rows.map((s) => (
                <TableRow key={s.id} hover>
                  <TableCell>{s.name}</TableCell>
                  <TableCell sx={{ wordBreak: 'break-all', maxWidth: 260 }}>{s.url}</TableCell>
                  <TableCell>{s.events.map((e) => <Chip key={e} size="small" label={e} sx={{ mr: 0.5, mb: 0.5 }} />)}</TableCell>
                  <TableCell>
                    <Chip size="small" color={s.active ? 'success' : 'default'} label={s.active ? t('webhooks.active') : t(`webhooks.disabled.${s.disabled_reason || 'manual'}`, { defaultValue: t('webhooks.inactive') })} />
                    <Typography variant="caption" display="block" color="text.secondary">
                      {t('webhooks.last_success')}: {when(s.last_success_at)}
                      {s.failure_streak > 0 && ` · ${t('webhooks.streak', { count: s.failure_streak })}`}
                    </Typography>
                    <Typography variant="caption" display="block" color="text.secondary">
                      {Object.entries(s.deliveries || {}).map(([k, n]) => `${t(`webhooks.status.${k}`, { defaultValue: k })}: ${n}`).join(' · ')}
                    </Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                    <Tooltip title={t('webhooks.test')}><IconButton aria-label={t('webhooks.test')} onClick={() => test(s)}><SendIcon /></IconButton></Tooltip>
                    <Tooltip title={t('webhooks.history')}><IconButton aria-label={t('webhooks.history')} onClick={() => openHistory(s)}><HistoryIcon /></IconButton></Tooltip>
                    <Tooltip title={t('webhooks.rotate')}><IconButton aria-label={t('webhooks.rotate')} onClick={() => rotate(s)}><KeyIcon /></IconButton></Tooltip>
                    <Tooltip title={t('webhooks.edit')}><IconButton aria-label={t('webhooks.edit')} onClick={() => setForm({ id: s.id, version: s.row_version, name: s.name, url: s.url, events: s.events, active: s.active })}><EditIcon /></IconButton></Tooltip>
                    <Tooltip title={t('webhooks.delete')}><IconButton aria-label={t('webhooks.delete')} onClick={() => remove(s)}><DeleteIcon /></IconButton></Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>

      <Dialog open={!!form} onClose={() => setForm(null)} fullWidth maxWidth="sm">
        <DialogTitle>{form?.id ? t('webhooks.edit') : t('webhooks.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '8px !important' }}>
          {form && (
            <>
              <TextField label={t('webhooks.name')} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
              <TextField label={t('webhooks.url')} value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} required helperText={t('webhooks.url_hint')} />
              <FormControl>
                <InputLabel>{t('webhooks.events')}</InputLabel>
                <Select
                  multiple label={t('webhooks.events')} value={form.events} renderValue={(v) => v.join(', ')}
                  onChange={(e) => setForm({ ...form, events: typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value })}
                >
                  {events.map((ev) => (
                    <MenuItem key={ev.name} value={ev.name}>
                      <Checkbox size="small" checked={form.events.includes(ev.name)} />
                      <ListItemText primary={ev.name} secondary={t(`webhooks.event_desc.${ev.name}`, { defaultValue: ev.description })} />
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <FormControlLabel label={t('webhooks.active')} control={<Switch checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />} />
              <Typography variant="caption" color="text.secondary">{t('webhooks.payload_hint')}</Typography>
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setForm(null)}>{t('common.cancel')}</Button>
          <Button variant="contained" disabled={!form || !form.name.trim() || !form.url.trim() || form.events.length === 0} onClick={save}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!secret} onClose={() => setSecret(null)} fullWidth maxWidth="sm">
        <DialogTitle>{t('webhooks.secret_title', { name: secret?.name })}</DialogTitle>
        <DialogContent>
          <Alert severity="warning" sx={{ mb: 2 }}>{t('webhooks.secret_once')}</Alert>
          <TextField fullWidth value={secret?.value || ''} InputProps={{ readOnly: true }} onFocus={(e) => e.target.select()} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => secret && navigator.clipboard?.writeText(secret.value)}>{t('webhooks.copy')}</Button>
          <Button variant="contained" onClick={() => setSecret(null)}>{t('webhooks.secret_saved')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={!!history} onClose={() => setHistory(null)} fullWidth maxWidth="md">
        <DialogTitle>{t('webhooks.history_title', { name: history?.sub.name })}</DialogTitle>
        <DialogContent>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('webhooks.event')}</TableCell><TableCell>{t('webhooks.state')}</TableCell><TableCell>{t('webhooks.attempts')}</TableCell>
                <TableCell>{t('webhooks.response')}</TableCell><TableCell>{t('webhooks.created')}</TableCell><TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {history?.items.length === 0 && <TableRow><TableCell colSpan={6}>{t('webhooks.no_deliveries')}</TableCell></TableRow>}
              {history?.items.map((d) => (
                <TableRow key={d.id}>
                  <TableCell>{d.event_name}</TableCell>
                  <TableCell><Chip size="small" color={STATUS_COLOR[d.status]} label={t(`webhooks.status.${d.status}`, { defaultValue: d.status })} /></TableCell>
                  <TableCell>{d.attempts}{d.status === 'PENDING' && d.attempts > 0 ? ` · ${t('webhooks.next')} ${when(d.next_attempt_at)}` : ''}</TableCell>
                  <TableCell>{d.last_error || d.last_status_code || '—'}</TableCell>
                  <TableCell>{when(d.created_at)}</TableCell>
                  <TableCell>{d.status === 'DEAD' && d.event_name !== 'webhook.ping' && <Button size="small" onClick={() => retry(d)}>{t('webhooks.retry')}</Button>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DialogContent>
        <DialogActions><Button onClick={() => setHistory(null)}>{t('webhooks.close')}</Button></DialogActions>
      </Dialog>
    </PramaLayout>
  );
};
export default WebhooksPage;
