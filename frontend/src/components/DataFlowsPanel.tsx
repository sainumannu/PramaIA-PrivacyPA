import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, IconButton,
  InputLabel, MenuItem, OutlinedInput, Select, TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from './ErrorBanner';

interface Flow {
  id: string; row_version: number; source_type: string; source_id: string | null; source_label: string | null;
  target_type: string; target_id: string | null; target_label: string | null; frequency: string; channel: string;
  description: string; security_notes: string | null; data_category_ids: string[];
}
interface Named { id: string; name?: string; legal_name?: string }
interface GraphNode { key: string; type: string; label: string | null }
interface GraphEdge { id: string; source: string; target: string; channel: string; frequency: string; description: string }
interface Graph { nodes: GraphNode[]; edges: GraphEdge[] }

interface Props {
  processingId: string;
  categories: { id: string; name: string }[];
  canEdit: boolean;
  // Ogni modifica cambia la versione del trattamento: il chiamante la rilegge.
  onChanged?: () => void;
}

const TYPES = ['DATA_SUBJECT', 'ASSET', 'PARTY', 'ORGANIZATIONAL_UNIT', 'EXTERNAL'];
const REFERENCED = ['ASSET', 'PARTY', 'ORGANIZATIONAL_UNIT'];
const FREQUENCIES = ['REALTIME', 'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY', 'ON_DEMAND', 'ONE_OFF'];
const CHANNELS = ['API', 'FILE_TRANSFER', 'EMAIL', 'PEC', 'WEB_FORM', 'PORTAL', 'DATABASE', 'PAPER', 'MANUAL', 'OTHER'];
const TYPE_COLOR: Record<string, string> = {
  DATA_SUBJECT: '#f59e0b', ASSET: '#06b6d4', PARTY: '#a78bfa', ORGANIZATIONAL_UNIT: '#10b981', EXTERNAL: '#94a3b8',
};
const empty = {
  source_type: 'DATA_SUBJECT', source_id: '', source_label: '', target_type: 'ASSET', target_id: '', target_label: '',
  frequency: 'DAILY', channel: 'API', description: '', security_notes: '', data_category_ids: [] as string[],
};

// Colonne del grafo: chi solo invia a sinistra, chi solo riceve a destra, chi fa entrambe le cose al centro.
const layout = (graph: Graph) => {
  const outgoing = new Set(graph.edges.map((e) => e.source));
  const incoming = new Set(graph.edges.map((e) => e.target));
  const columns: GraphNode[][] = [[], [], []];
  graph.nodes.forEach((n) => columns[outgoing.has(n.key) && incoming.has(n.key) ? 1 : incoming.has(n.key) ? 2 : 0].push(n));
  const positions: Record<string, { x: number; y: number }> = {};
  columns.forEach((col, ci) => col.forEach((n, ri) => { positions[n.key] = { x: 20 + ci * 250, y: 20 + ri * 56 }; }));
  return { positions, height: 40 + Math.max(...columns.map((c) => c.length), 1) * 56 };
};

// Flussi dei dati del trattamento (§20): elenco modificabile e grafo.
export const DataFlowsPanel: React.FC<Props> = ({ processingId, categories, canEdit, onChanged }) => {
  const { t } = useTranslation('privacypa');
  const [flows, setFlows] = useState<Flow[]>([]);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [view, setView] = useState<'list' | 'graph'>('list');
  const [assets, setAssets] = useState<Named[]>([]);
  const [parties, setParties] = useState<Named[]>([]);
  const [units, setUnits] = useState<Named[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Flow | null>(null);
  const [form, setForm] = useState(empty);

  const base = `/api/privacypa/processing-activities/${processingId}/data-flows`;
  const load = async () => {
    try {
      setFlows(await apiClient.get<Flow[]>(base));
      setGraph(await apiClient.get<Graph>(`${base}/graph`));
    } catch (err: any) {
      setError(err.message);
    }
  };
  useEffect(() => {
    load();
    Promise.all([
      apiClient.get<Named[]>('/api/privacypa/assets'),
      apiClient.get<Named[]>('/api/privacypa/parties'),
      apiClient.get<Named[]>('/api/privacypa/organizational-units'),
    ]).then(([a, p, u]) => { setAssets(a); setParties(p); setUnits(u); }).catch(() => undefined);
  }, [processingId]);

  const optionsFor = (type: string): Named[] => (type === 'ASSET' ? assets : type === 'PARTY' ? parties : units);
  const nameOf = (type: string, id: string | null, label: string | null) => {
    if (!id) return label || '—';
    const found = optionsFor(type).find((o) => o.id === id);
    return found?.name || found?.legal_name || label || id;
  };

  const openDialog = (flow: Flow | null) => {
    setEditing(flow);
    setDialogError(null);
    setForm(flow ? {
      source_type: flow.source_type, source_id: flow.source_id || '', source_label: flow.source_label || '',
      target_type: flow.target_type, target_id: flow.target_id || '', target_label: flow.target_label || '',
      frequency: flow.frequency, channel: flow.channel, description: flow.description,
      security_notes: flow.security_notes || '', data_category_ids: flow.data_category_ids,
    } : empty);
    setOpen(true);
  };

  const save = async () => {
    const side = (kind: 'source' | 'target') => {
      const type = form[`${kind}_type` as 'source_type'];
      return REFERENCED.includes(type)
        ? { [`${kind}_id`]: form[`${kind}_id` as 'source_id'] || null, [`${kind}_label`]: null }
        : { [`${kind}_id`]: null, [`${kind}_label`]: form[`${kind}_label` as 'source_label'].trim() || null };
    };
    const body = {
      source_type: form.source_type, target_type: form.target_type, ...side('source'), ...side('target'),
      frequency: form.frequency, channel: form.channel, description: form.description.trim(),
      security_notes: form.security_notes.trim() || null, data_category_ids: form.data_category_ids,
    };
    try {
      if (editing) await apiClient.patch(`/api/privacypa/data-flows/${editing.id}`, body, { version: editing.row_version });
      else await apiClient.post(base, body);
      setOpen(false);
      await load();
      onChanged?.();
    } catch (err: any) {
      setDialogError(err.message);
    }
  };

  const remove = async (flow: Flow) => {
    if (!window.confirm(t('flows.confirm_delete'))) return;
    try {
      await apiClient.delete(`/api/privacypa/data-flows/${flow.id}`);
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const drawing = useMemo(() => (graph ? layout(graph) : null), [graph]);

  const endpointFields = (kind: 'source' | 'target') => {
    const typeKey = `${kind}_type` as 'source_type';
    const type = form[typeKey];
    return (
      <Box sx={{ display: 'flex', gap: 2 }}>
        <FormControl sx={{ minWidth: 190 }}>
          <InputLabel>{t(`flows.${kind}`)}</InputLabel>
          <Select label={t(`flows.${kind}`)} value={type} onChange={(e) => setForm({ ...form, [typeKey]: e.target.value, [`${kind}_id`]: '', [`${kind}_label`]: '' })}>
            {TYPES.map((x) => <MenuItem key={x} value={x}>{t(`flows.type.${x}`)}</MenuItem>)}
          </Select>
        </FormControl>
        {REFERENCED.includes(type) ? (
          <FormControl fullWidth>
            <InputLabel>{t('flows.element')}</InputLabel>
            <Select label={t('flows.element')} value={form[`${kind}_id` as 'source_id']} onChange={(e) => setForm({ ...form, [`${kind}_id`]: e.target.value })}>
              {optionsFor(type).map((o) => <MenuItem key={o.id} value={o.id}>{o.name || o.legal_name}</MenuItem>)}
            </Select>
          </FormControl>
        ) : (
          <TextField fullWidth label={t('flows.label')} value={form[`${kind}_label` as 'source_label']} onChange={(e) => setForm({ ...form, [`${kind}_label`]: e.target.value })} />
        )}
      </Box>
    );
  };

  return (
    <Box sx={{ border: 1, borderColor: 'divider', borderRadius: 1, p: 1.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography variant="subtitle2" sx={{ flex: 1 }}>{t('flows.title')}</Typography>
        <ToggleButtonGroup size="small" exclusive value={view} onChange={(_, v) => v && setView(v)}>
          <ToggleButton value="list">{t('flows.view_list')}</ToggleButton>
          <ToggleButton value="graph">{t('flows.view_graph')}</ToggleButton>
        </ToggleButtonGroup>
        {canEdit && <Button size="small" startIcon={<AddIcon />} onClick={() => openDialog(null)}>{t('flows.add')}</Button>}
      </Box>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {flows.length === 0 && <Typography variant="body2" color="text.secondary">{t('flows.empty')}</Typography>}

      {view === 'list' && flows.map((f) => (
        <Box key={f.id} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.75, borderTop: 1, borderColor: 'divider' }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="body2">
              <strong>{nameOf(f.source_type, f.source_id, f.source_label)}</strong>{' → '}<strong>{nameOf(f.target_type, f.target_id, f.target_label)}</strong>
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {f.description} · {t(`flows.frequency.${f.frequency}`)} · {t(`flows.channel.${f.channel}`)}
              {f.data_category_ids.length > 0 && ` · ${f.data_category_ids.map((id) => categories.find((c) => c.id === id)?.name || id).join(', ')}`}
            </Typography>
          </Box>
          {canEdit && (
            <>
              <Tooltip title={t('common.edit')}><IconButton size="small" onClick={() => openDialog(f)}><EditIcon fontSize="small" /></IconButton></Tooltip>
              <Tooltip title={t('flows.delete')}><IconButton size="small" onClick={() => remove(f)}><DeleteIcon fontSize="small" /></IconButton></Tooltip>
            </>
          )}
        </Box>
      ))}

      {view === 'graph' && graph && drawing && graph.nodes.length > 0 && (
        <Box sx={{ overflowX: 'auto', mt: 1 }}>
          <svg width={3 * 250 + 20} height={drawing.height} role="img" aria-label={t('flows.title')}>
            <defs>
              <marker id="flow-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" fill="#94a3b8" />
              </marker>
            </defs>
            {graph.edges.map((e) => {
              const a = drawing.positions[e.source], b = drawing.positions[e.target];
              if (!a || !b) return null;
              const x1 = a.x + 170, y1 = a.y + 18, x2 = b.x - 4, y2 = b.y + 18;
              return (
                <g key={e.id}>
                  <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#94a3b8" strokeWidth={1.5} markerEnd="url(#flow-arrow)" />
                  <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 4} fontSize="10" textAnchor="middle" fill="#cbd5e1">{t(`flows.channel.${e.channel}`)}</text>
                </g>
              );
            })}
            {graph.nodes.map((n) => {
              const p = drawing.positions[n.key];
              return (
                <g key={n.key}>
                  <rect x={p.x} y={p.y} width={170} height={36} rx={6} fill="none" stroke={TYPE_COLOR[n.type]} strokeWidth={2} />
                  <text x={p.x + 85} y={p.y + 22} fontSize="12" textAnchor="middle" fill="#e2e8f0">{(n.label || '').slice(0, 24)}</text>
                </g>
              );
            })}
          </svg>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mt: 0.5 }}>
            {TYPES.map((x) => <Chip key={x} size="small" variant="outlined" label={t(`flows.type.${x}`)} sx={{ borderColor: TYPE_COLOR[x] }} />)}
          </Box>
        </Box>
      )}

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? t('flows.edit') : t('flows.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          {dialogError && <Alert severity="error">{dialogError}</Alert>}
          {endpointFields('source')}
          {endpointFields('target')}
          <Box sx={{ display: 'flex', gap: 2 }}>
            <FormControl fullWidth>
              <InputLabel>{t('flows.frequency_label')}</InputLabel>
              <Select label={t('flows.frequency_label')} value={form.frequency} onChange={(e) => setForm({ ...form, frequency: e.target.value })}>
                {FREQUENCIES.map((x) => <MenuItem key={x} value={x}>{t(`flows.frequency.${x}`)}</MenuItem>)}
              </Select>
            </FormControl>
            <FormControl fullWidth>
              <InputLabel>{t('flows.channel_label')}</InputLabel>
              <Select label={t('flows.channel_label')} value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
                {CHANNELS.map((x) => <MenuItem key={x} value={x}>{t(`flows.channel.${x}`)}</MenuItem>)}
              </Select>
            </FormControl>
          </Box>
          <TextField label={t('flows.description')} value={form.description} required multiline minRows={2} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <FormControl fullWidth>
            <InputLabel>{t('flows.categories')}</InputLabel>
            <Select
              multiple value={form.data_category_ids} input={<OutlinedInput label={t('flows.categories')} />}
              onChange={(e) => setForm({ ...form, data_category_ids: typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value })}
              renderValue={(ids) => categories.filter((c) => ids.includes(c.id)).map((c) => c.name).join(', ')}
            >
              {categories.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField label={t('flows.security_notes')} value={form.security_notes} multiline minRows={2} onChange={(e) => setForm({ ...form, security_notes: e.target.value })} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={save} disabled={!form.description.trim()}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default DataFlowsPanel;
