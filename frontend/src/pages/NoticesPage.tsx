import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle, FormControl,
  InputLabel, Link, MenuItem, OutlinedInput, Paper, Select, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from '@mui/material';
import DescriptionIcon from '@mui/icons-material/Description';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from '../components/PramaLayout';
import StatusNotesDialog from '../components/StatusNotesDialog';
import { apiClient } from '../services/apiClient';
import { AnnotationsButton } from '../components/AnnotationsPanel';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';

interface Version {
  id: string; version_no: number; version_label: string; content?: string; effective_from: string | null;
  effective_to: string | null; published_url: string | null; approved_by: string | null; approved_at: string | null;
  is_approved: boolean;
}
interface Notice {
  id: string; row_version: number; code: string; title: string; audience: string | null; status: string;
  owner_unit_id: string; current_version_id: string | null; working_version_id: string | null;
  current_version: Version | null; processing_activity_ids: string[]; versions?: Version[];
  last_reviewed_at: string | null; last_reviewed_by: string | null; review_notes: string | null;
}
interface Unit { id: string; name: string }
interface Processing { id: string; code: string; title: string }
interface DiffOp { op: 'equal' | 'insert' | 'delete'; text: string }

const TRANSITIONS: Record<string, string[]> = {
  DRAFT: ['IN_REVIEW'], IN_REVIEW: ['DRAFT', 'APPROVED'], APPROVED: ['RETIRED'], RETIRED: [],
};
const STATUS_COLOR: Record<string, 'default' | 'warning' | 'info' | 'success'> = {
  DRAFT: 'default', IN_REVIEW: 'warning', APPROVED: 'success', RETIRED: 'info',
};
const PERMISSION_FOR: Record<string, string> = {
  IN_REVIEW: 'notice.submit', DRAFT: 'notice.submit', APPROVED: 'notice.approve', RETIRED: 'notice.archive',
};

export const NoticesPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [items, setItems] = useState<Notice[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [processings, setProcessings] = useState<Processing[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState({ title: '', audience: '', owner_unit_id: '', version_label: '1.0', effective_from: '', content: '' });
  const [detail, setDetail] = useState<Notice | null>(null);
  const [meta, setMeta] = useState({ title: '', audience: '', owner_unit_id: '' });
  const [draft, setDraft] = useState({ version_label: '', effective_from: '', published_url: '', content: '' });
  const [links, setLinks] = useState<string[]>([]);
  const [newVersionOpen, setNewVersionOpen] = useState(false);
  const [newLabel, setNewLabel] = useState('');
  const [statusChange, setStatusChange] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffOp[] | null>(null);

  const load = async () => {
    try {
      const [n, u, p] = await Promise.all([
        apiClient.get<Notice[]>('/api/privacypa/notices'),
        apiClient.get<Unit[]>('/api/privacypa/organizational-units'),
        apiClient.get<Processing[]>('/api/privacypa/processing-activities'),
      ]);
      setItems(n); setUnits(u); setProcessings(p);
    } catch (err: any) {
      setError(err.message);
    }
  };
  useEffect(() => { load(); }, []);

  const unitName = (id: string) => units.find((u) => u.id === id)?.name || '—';

  const show = (notice: Notice) => {
    setDetail(notice);
    setMeta({ title: notice.title, audience: notice.audience || '', owner_unit_id: notice.owner_unit_id });
    const working = notice.versions?.find((v) => !v.is_approved);
    setDraft({
      version_label: working?.version_label || '', effective_from: working?.effective_from || '',
      published_url: working?.published_url || '', content: working?.content || '',
    });
    setLinks(notice.processing_activity_ids);
    setDialogError(null);
  };

  const openDetail = async (id: string) => {
    try { show(await apiClient.get<Notice>(`/api/privacypa/notices/${id}`)); } catch (err: any) { setError(err.message); }
  };

  // Ogni operazione restituisce l'informativa aggiornata (con la nuova versione riga).
  const run = async (action: () => Promise<Notice>, message?: string) => {
    setDialogError(null);
    try {
      const updated = await action();
      show(updated);
      if (message) setInfo(message);
      await load();
      return true;
    } catch (err: any) {
      setDialogError(err.message);
      return false;
    }
  };

  const create = async () => {
    try {
      const created = await apiClient.post<Notice>('/api/privacypa/notices', {
        title: createForm.title.trim(), audience: createForm.audience.trim() || null, owner_unit_id: createForm.owner_unit_id,
        version_label: createForm.version_label.trim() || null, content: createForm.content || null,
        effective_from: createForm.effective_from || null,
      });
      setCreateOpen(false);
      setCreateForm({ title: '', audience: '', owner_unit_id: '', version_label: '1.0', effective_from: '', content: '' });
      await load();
      show(created);
    } catch (err: any) {
      setDialogError(err.message);
    }
  };

  const base = detail ? `/api/privacypa/notices/${detail.id}` : '';
  const version = detail?.row_version;
  const editable = detail && ['DRAFT'].includes(detail.status) && can('notice.edit');
  const working = detail?.versions?.find((v) => !v.is_approved);

  const openDiff = async (oldV: Version, newV: Version) => {
    try {
      const r = await apiClient.get<{ changes: DiffOp[] }>(`${base}/versions/${oldV.id}/diff/${newV.id}`);
      setDiff(r.changes);
    } catch (err: any) { setDialogError(err.message); }
  };

  const requestStatus = (target: string) => {
    if (!detail) return;
    if (detail.status === 'IN_REVIEW' || target === 'IN_REVIEW') setStatusChange(target);
    else run(() => apiClient.post<Notice>(`${base}/status`, { status: target }, { version }), t('notices.status_changed'));
  };

  return (
    <PramaLayout pageTitle={t('notices.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <DescriptionIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('notices.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          {can('notice.create') && (
            <Button variant="contained" startIcon={<AddIcon />} onClick={() => { setDialogError(null); setCreateOpen(true); }}>{t('notices.add')}</Button>
          )}
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('notices.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        {info && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setInfo(null)}>{info}</Alert>}

        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('notices.code')}</TableCell><TableCell>{t('notices.name')}</TableCell>
                <TableCell>{t('notices.unit')}</TableCell><TableCell>{t('notices.status')}</TableCell>
                <TableCell>{t('notices.in_force')}</TableCell><TableCell>{t('notices.processings')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {items.length === 0 && <TableRow><TableCell colSpan={6}><Typography variant="body2" color="text.secondary">{t('notices.empty')}</Typography></TableCell></TableRow>}
              {items.map((n) => (
                <TableRow key={n.id} hover>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{n.code} <AnnotationsButton entityType="privacy_notice" entityId={n.id} title={n.code} /></TableCell>
                  <TableCell>
                    <Button size="small" sx={{ textTransform: 'none' }} onClick={() => openDetail(n.id)}>{n.title}</Button>
                    {n.audience && <Typography variant="caption" display="block" color="text.secondary">{n.audience}</Typography>}
                  </TableCell>
                  <TableCell>{unitName(n.owner_unit_id)}</TableCell>
                  <TableCell><Chip size="small" color={STATUS_COLOR[n.status]} label={t(`notices.st.${n.status}`)} /></TableCell>
                  <TableCell>
                    {n.current_version ? `${n.current_version.version_label} (${n.current_version.effective_from || '—'})` : '—'}
                  </TableCell>
                  <TableCell>{n.processing_activity_ids.length}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('notices.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          {dialogError && <Alert severity="error">{dialogError}</Alert>}
          <TextField label={t('notices.name')} value={createForm.title} required onChange={(e) => setCreateForm({ ...createForm, title: e.target.value })} />
          <TextField label={t('notices.audience')} value={createForm.audience} onChange={(e) => setCreateForm({ ...createForm, audience: e.target.value })} />
          <FormControl fullWidth required>
            <InputLabel>{t('notices.unit')}</InputLabel>
            <Select label={t('notices.unit')} value={createForm.owner_unit_id} onChange={(e) => setCreateForm({ ...createForm, owner_unit_id: e.target.value })}>
              {units.map((u) => <MenuItem key={u.id} value={u.id}>{u.name}</MenuItem>)}
            </Select>
          </FormControl>
          <Box sx={{ display: 'flex', gap: 2 }}>
            <TextField label={t('notices.version_label')} value={createForm.version_label} onChange={(e) => setCreateForm({ ...createForm, version_label: e.target.value })} />
            <TextField type="date" label={t('notices.effective_from')} InputLabelProps={{ shrink: true }} value={createForm.effective_from} onChange={(e) => setCreateForm({ ...createForm, effective_from: e.target.value })} />
          </Box>
          <TextField label={t('notices.content')} value={createForm.content} multiline minRows={4} onChange={(e) => setCreateForm({ ...createForm, content: e.target.value })} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={create} disabled={!createForm.title.trim() || !createForm.owner_unit_id}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(detail)} onClose={() => setDetail(null)} maxWidth="md" fullWidth>
        <DialogTitle>
          {detail?.code} — {detail?.title}{' '}
          {detail && <Chip size="small" color={STATUS_COLOR[detail.status]} label={t(`notices.st.${detail.status}`)} sx={{ ml: 1 }} />}
        </DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          {dialogError && <Alert severity="error" onClose={() => setDialogError(null)}>{dialogError}</Alert>}
          {detail && (
            <>
              <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                {(detail.status === 'DRAFT' || detail.status === 'APPROVED') && can('notice.edit') && (
                  <>
                    <TextField label={t('notices.name')} value={meta.title} onChange={(e) => setMeta({ ...meta, title: e.target.value })} sx={{ flex: 2, minWidth: 220 }} />
                    <TextField label={t('notices.audience')} value={meta.audience} onChange={(e) => setMeta({ ...meta, audience: e.target.value })} sx={{ flex: 1, minWidth: 160 }} />
                    <Button onClick={() => run(() => apiClient.patch<Notice>(base, { title: meta.title, audience: meta.audience || null, owner_unit_id: meta.owner_unit_id }, { version }))}>{t('common.save')}</Button>
                  </>
                )}
              </Box>

              <Typography variant="subtitle2">{t('notices.versions')}</Typography>
              {(detail.versions || []).map((v, i, all) => (
                <Box key={v.id} sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', borderTop: '1px solid rgba(255,255,255,0.08)', py: 0.5 }}>
                  <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>v{v.version_no}</Typography>
                  <Typography variant="body2"><strong>{v.version_label}</strong></Typography>
                  <Chip size="small" color={v.is_approved ? 'success' : 'default'} label={v.is_approved ? t('notices.approved_by', { user: v.approved_by || '' }) : t('notices.working')} />
                  {v.id === detail.current_version_id && <Chip size="small" color="primary" label={t('notices.in_force')} />}
                  <Typography variant="caption" color="text.secondary">{v.effective_from || '—'} → {v.effective_to || '—'}</Typography>
                  {v.published_url && <Link href={v.published_url} target="_blank" rel="noopener noreferrer" variant="caption">{t('notices.published')}</Link>}
                  <Box sx={{ flex: 1 }} />
                  {i > 0 && <Button size="small" onClick={() => openDiff(all[i - 1], v)}>{t('notices.compare')}</Button>}
                </Box>
              ))}

              {editable && working && (
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, border: '1px solid rgba(255,255,255,0.12)', borderRadius: 1, p: 1.5 }}>
                  <Typography variant="subtitle2">{t('notices.edit_working')}</Typography>
                  <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                    <TextField label={t('notices.version_label')} value={draft.version_label} onChange={(e) => setDraft({ ...draft, version_label: e.target.value })} />
                    <TextField type="date" label={t('notices.effective_from')} InputLabelProps={{ shrink: true }} value={draft.effective_from} onChange={(e) => setDraft({ ...draft, effective_from: e.target.value })} />
                    <TextField label={t('notices.published_url')} value={draft.published_url} onChange={(e) => setDraft({ ...draft, published_url: e.target.value })} sx={{ flex: 1, minWidth: 240 }} />
                  </Box>
                  <TextField label={t('notices.content')} value={draft.content} multiline minRows={6} onChange={(e) => setDraft({ ...draft, content: e.target.value })} />
                  <Box>
                    <Button variant="contained" onClick={() => run(() => apiClient.patch<Notice>(`${base}/versions/${working.id}`, {
                      version_label: draft.version_label, content: draft.content, effective_from: draft.effective_from || null,
                      published_url: draft.published_url || null,
                    }, { version }), t('notices.version_saved'))}>{t('notices.save_version')}</Button>
                  </Box>
                </Box>
              )}

              <FormControl fullWidth disabled={detail.status === 'RETIRED' || !can('notice.edit')}>
                <InputLabel>{t('notices.processings')}</InputLabel>
                <Select
                  multiple value={links} input={<OutlinedInput label={t('notices.processings')} />}
                  onChange={(e) => setLinks(typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)}
                  renderValue={(ids) => processings.filter((p) => ids.includes(p.id)).map((p) => p.code).join(', ')}
                >
                  {processings.map((p) => <MenuItem key={p.id} value={p.id}>{p.code} — {p.title}</MenuItem>)}
                </Select>
              </FormControl>
              {can('notice.edit') && detail.status !== 'RETIRED' && (
                <Box>
                  <Button onClick={() => run(() => apiClient.put<Notice>(`${base}/processing-links`, { processing_activity_ids: links }, { version }), t('notices.links_saved'))}>{t('notices.save_links')}</Button>
                </Box>
              )}
              {detail.last_reviewed_at && (
                <Typography variant="caption" color="text.secondary">
                  {t('notices.last_review', { user: detail.last_reviewed_by || '', date: detail.last_reviewed_at.slice(0, 10) })}{detail.review_notes ? ` — ${detail.review_notes}` : ''}
                </Typography>
              )}
            </>
          )}
        </DialogContent>
        <DialogActions sx={{ flexWrap: 'wrap' }}>
          {detail && detail.status === 'APPROVED' && can('notice.edit') && (
            <Button onClick={() => { setNewLabel(''); setNewVersionOpen(true); }}>{t('notices.new_version')}</Button>
          )}
          {detail && detail.current_version_id && detail.status !== 'RETIRED' && can('notice.edit') && (
            <Button onClick={() => setReviewOpen(true)}>{t('notices.confirm_review')}</Button>
          )}
          {detail && (TRANSITIONS[detail.status] || []).filter((s) => can(PERMISSION_FOR[s])).map((s) => (
            <Button key={s} variant={s === 'APPROVED' ? 'contained' : 'outlined'} onClick={() => requestStatus(s)}>{t(`notices.actions.${s}`)}</Button>
          ))}
          <Box sx={{ flex: 1 }} />
          <Button onClick={() => setDetail(null)}>{t('notices.close')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={newVersionOpen} onClose={() => setNewVersionOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>{t('notices.new_version')}</DialogTitle>
        <DialogContent sx={{ pt: 2 }}>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('notices.new_version_hint')}</Typography>
          <TextField autoFocus fullWidth label={t('notices.version_label')} value={newLabel} onChange={(e) => setNewLabel(e.target.value)} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewVersionOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" disabled={!newLabel.trim()} onClick={async () => {
            if (await run(() => apiClient.post<Notice>(`${base}/versions`, { version_label: newLabel.trim(), copy_current: true }, { version }), t('notices.version_created'))) setNewVersionOpen(false);
          }}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>

      <StatusNotesDialog
        open={Boolean(statusChange)}
        title={statusChange ? t(statusChange === 'IN_REVIEW' ? 'status_notes.submit_title' : statusChange === 'APPROVED' ? 'status_notes.approve_title' : 'status_notes.back_title') : ''}
        hint={statusChange ? t(statusChange === 'IN_REVIEW' ? 'status_notes.submit_hint' : statusChange === 'APPROVED' ? 'status_notes.approve_hint' : 'status_notes.back_hint') : undefined}
        error={dialogError}
        onConfirm={async (notes) => {
          if (statusChange && await run(() => apiClient.post<Notice>(`${base}/status`, { status: statusChange, notes: notes || null }, { version }), t('notices.status_changed'))) setStatusChange(null);
        }}
        onClose={() => setStatusChange(null)}
      />

      <StatusNotesDialog
        open={reviewOpen}
        title={t('notices.confirm_review')}
        hint={t('notices.confirm_review_hint')}
        error={dialogError}
        onConfirm={async (notes) => {
          if (await run(() => apiClient.post<Notice>(`${base}/confirm-review`, { notes: notes || null }, { version }), t('notices.review_confirmed'))) setReviewOpen(false);
        }}
        onClose={() => setReviewOpen(false)}
      />

      <Dialog open={Boolean(diff)} onClose={() => setDiff(null)} maxWidth="md" fullWidth>
        <DialogTitle>{t('notices.compare')}</DialogTitle>
        <DialogContent>
          <Typography component="div" variant="body2" sx={{ whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
            {(diff || []).map((c, i) => (
              <span
                key={i}
                style={c.op === 'insert' ? { background: 'rgba(16,185,129,0.25)' } : c.op === 'delete' ? { background: 'rgba(239,68,68,0.25)', textDecoration: 'line-through' } : undefined}
              >
                {c.text}
              </span>
            ))}
          </Typography>
        </DialogContent>
        <DialogActions><Button onClick={() => setDiff(null)}>{t('notices.close')}</Button></DialogActions>
      </Dialog>
    </PramaLayout>
  );
};

export default NoticesPage;
