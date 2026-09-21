import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Chip,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Switch,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import AssignmentIcon from '@mui/icons-material/Assignment';
import MenuBookIcon from '@mui/icons-material/MenuBook';
import { useSearchParams } from 'react-router-dom';
import { PramaLayout } from '../components/PramaLayout';
import StatusNotesDialog from '../components/StatusNotesDialog';
import RetentionRulesPanel from '../components/RetentionRulesPanel';
import DataFlowsPanel from '../components/DataFlowsPanel';
import { usePermissions } from '../services/permissions';
import { apiClient } from '../services/apiClient';
import { AnnotationsButton } from '../components/AnnotationsPanel';
import { useTranslation } from '../i18n/useTranslation';

interface ProcessingActivity {
  id: string;
  row_version: number;
  code: string;
  title: string;
  description: string | null;
  status: string;
  organizational_unit_id: string | null;
  owner_user_id: string | null;
  privacy_referent_user_id: string | null;
  purpose_summary: string | null;
  source_of_data: string | null;
  collection_method: string | null;
  mandatory_provision: boolean | null;
  automated_decision: boolean;
  profiling: boolean;
  start_date: string | null;
  last_review_date: string | null;
  next_review_date: string | null;
  legacy_notes?: string | null;
  confidentiality_level: string;
  version_no: number;
  purposes?: { id: string; description: string; order_no: number }[];
  legal_bases?: { legal_basis_code: string }[];
  data_subjects?: { category_id: string }[];
  data_categories?: { data_category_id: string }[];
  assets?: { asset_id: string }[];
  processors?: { party_id: string }[];
  recipients?: { party_id: string }[];
}

interface Unit { id: string; name: string; code: string }
interface LegalBasis { code: string; description: string }
interface SubjectCat { id: string; code: string; name: string }
interface DataCat { id: string; code: string; name: string; parent_id: string | null; gdpr_class: string }
interface Asset { id: string; code: string; name: string }
interface Party { id: string; code: string; legal_name: string }

const STATUSES = ['DRAFT', 'IN_REVIEW', 'APPROVED', 'ACTIVE', 'SUSPENDED', 'ARCHIVED'] as const;
const TRANSITIONS: Record<string, string[]> = {
  DRAFT: ['IN_REVIEW'],
  IN_REVIEW: ['DRAFT', 'APPROVED'],
  APPROVED: ['ACTIVE'],
  ACTIVE: ['IN_REVIEW', 'SUSPENDED', 'ARCHIVED'],
  SUSPENDED: ['ACTIVE', 'ARCHIVED'],
  ARCHIVED: [],
};
const STATUS_COLOR: Record<string, 'default' | 'warning' | 'info' | 'success' | 'error'> = {
  DRAFT: 'default',
  IN_REVIEW: 'warning',
  APPROVED: 'info',
  ACTIVE: 'success',
  SUSPENDED: 'warning',
  ARCHIVED: 'default',
};

const emptyForm = {
  title: '',
  description: '',
  organizational_unit_id: '',
  owner_user_id: '',
  privacy_referent_user_id: '',
  purpose_summary: '',
  source_of_data: '',
  collection_method: '',
  mandatory_provision: false,
  automated_decision: false,
  profiling: false,
  start_date: '',
  last_review_date: '',
  next_review_date: '',
  confidentiality_level: 'INTERNAL',
  purposesText: '',
  legal_basis_codes: [] as string[],
  data_subject_ids: [] as string[],
  data_category_ids: [] as string[],
  asset_ids: [] as string[],
  processor_ids: [] as string[],
  recipient_ids: [] as string[],
};

export const RegisterPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState(0);
  const [items, setItems] = useState<ProcessingActivity[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [legalBases, setLegalBases] = useState<LegalBasis[]>([]);
  const [subjects, setSubjects] = useState<SubjectCat[]>([]);
  const [dataCats, setDataCats] = useState<DataCat[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [parties, setParties] = useState<Party[]>([]);
  const [statusFilter, setStatusFilter] = useState(searchParams.get('status') || '');
  const [search, setSearch] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ProcessingActivity | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [versionsOf, setVersionsOf] = useState<ProcessingActivity | null>(null);
  const [versions, setVersions] = useState<{ id: string; version_no: number; reason: string; created_at: string; created_by: string | null }[]>([]);
  const [versionSnap, setVersionSnap] = useState<any | null>(null);
  const [statusChange, setStatusChange] = useState<{ row: ProcessingActivity; next: string } | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  const load = async () => {
    try {
      const params = new URLSearchParams();
      if (statusFilter) params.set('status', statusFilter);
      if (search.trim()) params.set('search', search.trim());
      const qs = params.toString();
      const [acts, unitList, lb, sub, cats, assetList, partyList] = await Promise.all([
        apiClient.get<ProcessingActivity[]>(`/api/privacypa/processing-activities${qs ? `?${qs}` : ''}`),
        apiClient.get<Unit[]>('/api/privacypa/organizational-units'),
        apiClient.get<LegalBasis[]>('/api/privacypa/catalogs/legal-basis'),
        apiClient.get<SubjectCat[]>('/api/privacypa/catalogs/data-subject-categories'),
        apiClient.get<DataCat[]>('/api/privacypa/catalogs/data-categories'),
        apiClient.get<Asset[]>('/api/privacypa/assets'),
        apiClient.get<Party[]>('/api/privacypa/parties'),
      ]);
      setItems(acts);
      setUnits(unitList);
      setLegalBases(lb);
      setSubjects(sub);
      setDataCats(cats);
      setAssets(assetList);
      setParties(partyList);
    } catch (err: any) {
      setError(err.message || t('register.load_failed'));
    }
  };

  useEffect(() => { load(); }, [statusFilter]);

  const unitName = (id: string | null) => units.find((u) => u.id === id)?.name || '—';
  const statusLabel = (s: string) => t(`register.status.${s}`) || s;

  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm);
    setDialogOpen(true);
  };

  const openEdit = async (row: ProcessingActivity) => {
    try {
      const full = await apiClient.get<ProcessingActivity>(`/api/privacypa/processing-activities/${row.id}`);
      setEditing(full);
      setForm({
        title: full.title || '',
        description: full.description || '',
        organizational_unit_id: full.organizational_unit_id || '',
        owner_user_id: full.owner_user_id || '',
        privacy_referent_user_id: full.privacy_referent_user_id || '',
        purpose_summary: full.purpose_summary || '',
        source_of_data: full.source_of_data || '',
        collection_method: full.collection_method || '',
        mandatory_provision: Boolean(full.mandatory_provision),
        automated_decision: Boolean(full.automated_decision),
        profiling: Boolean(full.profiling),
        start_date: full.start_date || '',
        last_review_date: full.last_review_date || '',
        next_review_date: full.next_review_date || '',
        confidentiality_level: full.confidentiality_level || 'INTERNAL',
        purposesText: (full.purposes || []).map((p) => p.description).join('\n'),
        legal_basis_codes: (full.legal_bases || []).map((b) => b.legal_basis_code),
        data_subject_ids: (full.data_subjects || []).map((s) => s.category_id),
        data_category_ids: (full.data_categories || []).map((c) => c.data_category_id),
        asset_ids: (full.assets || []).map((a) => a.asset_id),
        processor_ids: (full.processors || []).map((p) => p.party_id),
        recipient_ids: (full.recipients || []).map((p) => p.party_id),
      });
      setDialogOpen(true);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const payload = () => ({
    title: form.title.trim(),
    description: form.description.trim() || null,
    organizational_unit_id: form.organizational_unit_id || null,
    owner_user_id: form.owner_user_id.trim() || null,
    privacy_referent_user_id: form.privacy_referent_user_id.trim() || null,
    purpose_summary: form.purpose_summary.trim() || null,
    source_of_data: form.source_of_data.trim() || null,
    collection_method: form.collection_method.trim() || null,
    mandatory_provision: form.mandatory_provision,
    automated_decision: form.automated_decision,
    profiling: form.profiling,
    start_date: form.start_date || null,
    last_review_date: form.last_review_date || null,
    next_review_date: form.next_review_date || null,
    confidentiality_level: form.confidentiality_level,
    purposes: form.purposesText.split('\n').map((l) => l.trim()).filter(Boolean),
    legal_basis_codes: form.legal_basis_codes,
    data_subject_ids: form.data_subject_ids,
    data_category_ids: form.data_category_ids,
  });

  const save = async () => {
    if (!form.title.trim()) {
      setError(t('register.title_required'));
      return;
    }
    try {
      const saved = editing
        ? await apiClient.patch<ProcessingActivity>(`/api/privacypa/processing-activities/${editing.id}`, payload(), { version: editing.row_version })
        : await apiClient.post<ProcessingActivity>('/api/privacypa/processing-activities', payload());
      // Un'unica chiamata per i collegamenti, con la versione appena restituita dal salvataggio (§54).
      await apiClient.put(`/api/privacypa/processing-activities/${saved.id}/relations`, {
        assets: form.asset_ids.map((id) => ({ asset_id: id })),
        processors: form.processor_ids.map((id) => ({ party_id: id })),
        recipients: form.recipient_ids.map((id) => ({ party_id: id })),
      }, { version: saved.row_version });
      setSuccess(editing ? t('register.updated') : t('register.created'));
      setDialogOpen(false);
      await load();
    } catch (err: any) {
      setError(err.message || t('register.save_failed'));
    }
  };

  // Le regole di conservazione e i flussi aggiornano la versione del trattamento: si rilegge per non salvare con una vecchia.
  const refreshEditing = async () => {
    if (!editing) return;
    try {
      setEditing(await apiClient.get<ProcessingActivity>(`/api/privacypa/processing-activities/${editing.id}`));
    } catch (err: any) {
      setError(err.message);
    }
  };

  const changeStatus = async (id: string, next: string, version: number, notes?: string) => {
    try {
      await apiClient.post(
        `/api/privacypa/processing-activities/${id}/status`, { status: next, notes: notes || null }, { version },
      );
      setSuccess(t('register.status_changed'));
      setStatusChange(null);
      setStatusError(null);
      await load();
    } catch (err: any) {
      if (statusChange) setStatusError(err.message); else setError(err.message);
    }
  };

  // Invio in revisione e decisioni passano da una nota (§165); le altre transizioni sono dirette.
  const requestStatusChange = (row: ProcessingActivity, next: string) => {
    if (row.status === 'IN_REVIEW' || next === 'IN_REVIEW') {
      setStatusError(null);
      setStatusChange({ row, next });
    } else {
      changeStatus(row.id, next, row.row_version);
    }
  };

  const remove = async (row: ProcessingActivity) => {
    if (row.status !== 'DRAFT') return;
    if (!window.confirm(t('register.confirm_delete'))) return;
    try {
      await apiClient.delete(`/api/privacypa/processing-activities/${row.id}`);
      setSuccess(t('register.deleted'));
      await load();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const dataCatLabel = (c: DataCat) => {
    const parent = dataCats.find((p) => p.id === c.parent_id);
    return parent ? `${parent.name} / ${c.name}` : c.name;
  };

  const catalogGroups = useMemo(() => {
    const roots = dataCats.filter((c) => !c.parent_id);
    return { roots, children: dataCats.filter((c) => c.parent_id) };
  }, [dataCats]);

  return (
    <PramaLayout pageTitle={t('register.title')}>
      <Container maxWidth="lg">
        <Box sx={{ mb: 3 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <AssignmentIcon sx={{ fontSize: 28, color: 'primary.main' }} />
            <Typography variant="h4" sx={{ fontWeight: 600 }}>
              {t('register.title')}
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {t('register.subtitle')}
          </Typography>
        </Box>

        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        {success && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccess(null)}>{success}</Alert>}

        <Paper>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ borderBottom: 1, borderColor: 'divider', '& .MuiTab-root': { textTransform: 'none' } }}>
            <Tab icon={<AssignmentIcon fontSize="small" />} iconPosition="start" label={t('register.tabs.list')} />
            <Tab icon={<MenuBookIcon fontSize="small" />} iconPosition="start" label={t('register.tabs.catalogs')} />
          </Tabs>

          {tab === 0 && (
            <Box sx={{ p: 2 }}>
              <Box sx={{ display: 'flex', gap: 2, mb: 2, flexWrap: 'wrap', alignItems: 'center' }}>
                <TextField
                  size="small"
                  label={t('register.search')}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') load(); }}
                />
                <FormControl size="small" sx={{ minWidth: 180 }}>
                  <InputLabel>{t('register.status_filter')}</InputLabel>
                  <Select
                    label={t('register.status_filter')}
                    value={statusFilter}
                    onChange={(e) => setStatusFilter(e.target.value)}
                  >
                    <MenuItem value="">{t('register.all_statuses')}</MenuItem>
                    {STATUSES.map((s) => (
                      <MenuItem key={s} value={s}>{statusLabel(s)}</MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <Box sx={{ flex: 1 }} />
                <Button variant="outlined" onClick={() => apiClient.download('/api/privacypa/reports/art30?format=csv', 'registro-art30.csv')}>{t('register.export_csv')}</Button>
                <Button variant="outlined" onClick={() => apiClient.download('/api/privacypa/reports/art30?format=xlsx', 'registro-art30.xlsx')}>{t('register.export_xlsx')}</Button>
                <Button variant="outlined" onClick={() => apiClient.download('/api/privacypa/reports/art30?format=docx', 'registro-art30.docx')}>{t('register.export_docx')}</Button>
                <Button variant="outlined" onClick={() => apiClient.download('/api/privacypa/reports/art30?format=pdf', 'registro-art30.pdf')}>{t('register.export_pdf')}</Button>
                <Button variant="outlined" onClick={async () => {
                  const data = await apiClient.get<any>('/api/privacypa/reports/art30');
                  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = 'registro-art30.json';
                  a.click();
                  URL.revokeObjectURL(url);
                }}>{t('register.export_art30')}</Button>
                <Button variant="contained" startIcon={<AddIcon />} onClick={openCreate}>
                  {t('register.add')}
                </Button>
              </Box>

              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>{t('register.code')}</TableCell>
                      <TableCell>{t('register.fields.title')}</TableCell>
                      <TableCell>{t('register.fields.status')}</TableCell>
                      <TableCell>{t('register.fields.unit')}</TableCell>
                      <TableCell>{t('register.fields.next_review')}</TableCell>
                      <TableCell>{t('register.fields.version')}</TableCell>
                      <TableCell align="right">{t('common.actions')}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {items.length === 0 && (
                      <TableRow>
                        <TableCell colSpan={7}>
                          <Typography variant="body2" color="text.secondary">{t('register.empty')}</Typography>
                        </TableCell>
                      </TableRow>
                    )}
                    {items.map((row) => (
                      <TableRow key={row.id} hover>
                        <TableCell sx={{ fontFamily: 'monospace' }}>{row.code}</TableCell>
                        <TableCell>
                          <Button size="small" onClick={() => openEdit(row)} sx={{ textTransform: 'none' }}>
                            {row.title}
                          </Button>
                        </TableCell>
                        <TableCell>
                          <Chip size="small" label={statusLabel(row.status)} color={STATUS_COLOR[row.status] || 'default'} />
                        </TableCell>
                        <TableCell>{unitName(row.organizational_unit_id)}</TableCell>
                        <TableCell>{row.next_review_date || '—'}</TableCell>
                        <TableCell>v{row.version_no}</TableCell>
                        <TableCell align="right">
                          <AnnotationsButton entityType="processing_activity" entityId={row.id} title={row.code} />
                          {(TRANSITIONS[row.status] || []).map((next) => (
                            <Tooltip key={next} title={statusLabel(next)}>
                              <Button size="small" onClick={() => requestStatusChange(row, next)} sx={{ textTransform: 'none', mr: 0.5 }}>
                                {statusLabel(next)}
                              </Button>
                            </Tooltip>
                          ))}
                          <Button size="small" onClick={() => {
                            apiClient.get<any[]>(`/api/privacypa/processing-activities/${row.id}/versions`)
                              .then((v) => { setVersionsOf(row); setVersions(v); })
                              .catch((e) => setError(e.message));
                          }} sx={{ textTransform: 'none' }}>{t('register.history')}</Button>
                          {row.status === 'DRAFT' && (
                            <Button size="small" color="error" onClick={() => remove(row)} sx={{ textTransform: 'none' }}>
                              {t('register.delete')}
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </Box>
          )}

          {tab === 1 && (
            <Box sx={{ p: 3, display: 'grid', gap: 3 }}>
              <Box>
                <Typography variant="subtitle1" sx={{ mb: 1 }}>{t('catalogs.legal_basis')}</Typography>
                {legalBases.map((b) => (
                  <Chip key={b.code} label={`${b.code} — ${b.description}`} size="small" sx={{ m: 0.5 }} />
                ))}
              </Box>
              <Box>
                <Typography variant="subtitle1" sx={{ mb: 1 }}>{t('catalogs.subjects')}</Typography>
                {subjects.map((s) => (
                  <Chip key={s.id} label={s.name} size="small" sx={{ m: 0.5 }} />
                ))}
              </Box>
              <Box>
                <Typography variant="subtitle1" sx={{ mb: 1 }}>{t('catalogs.data_categories')}</Typography>
                {catalogGroups.roots.map((root) => (
                  <Box key={root.id} sx={{ mb: 1 }}>
                    <Typography variant="body2" fontWeight={600}>{root.name}</Typography>
                    <Box>
                      {dataCats.filter((c) => c.parent_id === root.id).map((c) => (
                        <Chip key={c.id} label={c.name} size="small" sx={{ m: 0.5 }} />
                      ))}
                    </Box>
                  </Box>
                ))}
              </Box>
            </Box>
          )}
        </Paper>
      </Container>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editing ? `${editing.code} — ${t('register.edit')}` : t('register.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          {editing?.legacy_notes && (
            <Alert severity="info" sx={{ whiteSpace: 'pre-line' }}>{editing.legacy_notes}</Alert>
          )}
          <TextField
            label={t('register.fields.title')}
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            required
            fullWidth
          />
          <TextField
            label={t('register.fields.description')}
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            multiline
            minRows={2}
            fullWidth
          />
          <FormControl fullWidth>
            <InputLabel>{t('register.fields.unit')}</InputLabel>
            <Select
              label={t('register.fields.unit')}
              value={form.organizational_unit_id}
              onChange={(e) => setForm({ ...form, organizational_unit_id: e.target.value })}
            >
              <MenuItem value="">{t('register.no_unit')}</MenuItem>
              {units.map((u) => (
                <MenuItem key={u.id} value={u.id}>{u.code} — {u.name}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField
            label={t('register.fields.purpose_summary')}
            value={form.purpose_summary}
            onChange={(e) => setForm({ ...form, purpose_summary: e.target.value })}
            multiline
            minRows={2}
            fullWidth
          />
          <TextField
            label={t('register.fields.purposes')}
            helperText={t('register.fields.purposes_hint')}
            value={form.purposesText}
            onChange={(e) => setForm({ ...form, purposesText: e.target.value })}
            multiline
            minRows={3}
            fullWidth
          />
          <FormControl fullWidth>
            <InputLabel>{t('register.fields.legal_bases')}</InputLabel>
            <Select
              multiple
              label={t('register.fields.legal_bases')}
              value={form.legal_basis_codes}
              onChange={(e) => setForm({ ...form, legal_basis_codes: e.target.value as string[] })}
            >
              {legalBases.map((b) => (
                <MenuItem key={b.code} value={b.code}>{b.code} — {b.description}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>{t('register.fields.subjects')}</InputLabel>
            <Select
              multiple
              label={t('register.fields.subjects')}
              value={form.data_subject_ids}
              onChange={(e) => setForm({ ...form, data_subject_ids: e.target.value as string[] })}
            >
              {subjects.map((s) => (
                <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>{t('register.fields.assets')}</InputLabel>
            <Select
              multiple
              label={t('register.fields.assets')}
              value={form.asset_ids}
              onChange={(e) => setForm({ ...form, asset_ids: e.target.value as string[] })}
            >
              {assets.map((a) => (
                <MenuItem key={a.id} value={a.id}>{a.code} — {a.name}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>{t('register.fields.processors')}</InputLabel>
            <Select
              multiple
              label={t('register.fields.processors')}
              value={form.processor_ids}
              onChange={(e) => setForm({ ...form, processor_ids: e.target.value as string[] })}
            >
              {parties.map((p) => (
                <MenuItem key={p.id} value={p.id}>{p.code} — {p.legal_name}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>{t('register.fields.recipients')}</InputLabel>
            <Select
              multiple
              label={t('register.fields.recipients')}
              value={form.recipient_ids}
              onChange={(e) => setForm({ ...form, recipient_ids: e.target.value as string[] })}
            >
              {parties.map((p) => (
                <MenuItem key={p.id} value={p.id}>{p.code} — {p.legal_name}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl fullWidth>
            <InputLabel>{t('register.fields.data_categories')}</InputLabel>
            <Select
              multiple
              label={t('register.fields.data_categories')}
              value={form.data_category_ids}
              onChange={(e) => setForm({ ...form, data_category_ids: e.target.value as string[] })}
            >
              {dataCats.map((c) => (
                <MenuItem key={c.id} value={c.id}>{dataCatLabel(c)}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr 1fr' }, gap: 2 }}>
            <TextField type="date" label={t('register.fields.start_date')} InputLabelProps={{ shrink: true }} value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
            <TextField type="date" label={t('register.fields.last_review')} InputLabelProps={{ shrink: true }} value={form.last_review_date} onChange={(e) => setForm({ ...form, last_review_date: e.target.value })} />
            <TextField type="date" label={t('register.fields.next_review')} InputLabelProps={{ shrink: true }} value={form.next_review_date} onChange={(e) => setForm({ ...form, next_review_date: e.target.value })} />
          </Box>
          <FormControl fullWidth>
            <InputLabel>{t('register.fields.confidentiality')}</InputLabel>
            <Select
              label={t('register.fields.confidentiality')}
              value={form.confidentiality_level}
              onChange={(e) => setForm({ ...form, confidentiality_level: e.target.value })}
            >
              {['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED'].map((lvl) => (
                <MenuItem key={lvl} value={lvl}>{t(`register.confidentiality.${lvl}`)}</MenuItem>
              ))}
            </Select>
          </FormControl>
          <Box>
            <FormControlLabel control={<Switch checked={form.automated_decision} onChange={(e) => setForm({ ...form, automated_decision: e.target.checked })} />} label={t('register.fields.automated_decision')} />
            <FormControlLabel control={<Switch checked={form.profiling} onChange={(e) => setForm({ ...form, profiling: e.target.checked })} />} label={t('register.fields.profiling')} />
            <FormControlLabel control={<Switch checked={form.mandatory_provision} onChange={(e) => setForm({ ...form, mandatory_provision: e.target.checked })} />} label={t('register.fields.mandatory_provision')} />
          </Box>
          {editing && (
            <>
              <RetentionRulesPanel
                processingId={editing.id}
                categories={(editing.data_categories || []).map((c) => ({
                  id: c.data_category_id,
                  name: dataCats.find((d) => d.id === c.data_category_id)?.name || c.data_category_id,
                }))}
                canEdit={can('retention.edit')}
                onChanged={refreshEditing}
              />
              <DataFlowsPanel
                processingId={editing.id}
                categories={(editing.data_categories || []).map((c) => ({
                  id: c.data_category_id,
                  name: dataCats.find((d) => d.id === c.data_category_id)?.name || c.data_category_id,
                }))}
                canEdit={can('dataflow.edit')}
                onChanged={refreshEditing}
              />
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={save}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>

      <Dialog open={Boolean(versionsOf)} onClose={() => { setVersionsOf(null); setVersionSnap(null); }} maxWidth="md" fullWidth>
        <DialogTitle>{t('register.history')} {versionsOf?.code}</DialogTitle>
        <DialogContent>
          {versions.length === 0 && <Typography variant="body2" color="text.secondary">{t('register.no_versions')}</Typography>}
          {versions.map((v) => (
            <Box key={v.id} sx={{ py: 1, borderBottom: '1px solid rgba(255,255,255,0.08)', display: 'flex', gap: 2, alignItems: 'center' }}>
              <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>v{v.version_no}</Typography>
              <Chip size="small" label={v.reason} />
              <Typography variant="caption">{v.created_at?.replace('T', ' ').slice(0, 19)} · {v.created_by}</Typography>
              <Button size="small" onClick={() => apiClient.get(`/api/privacypa/processing-activities/${versionsOf?.id}/versions/${v.id}`).then(setVersionSnap)}>{t('register.view_snapshot')}</Button>
            </Box>
          ))}
          {versionSnap && (
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, marginTop: 16 }}>
              {JSON.stringify(versionSnap.snapshot || versionSnap, null, 2)}
            </pre>
          )}
        </DialogContent>
      </Dialog>
      <StatusNotesDialog
        open={Boolean(statusChange)}
        title={statusChange ? t(statusChange.next === 'IN_REVIEW' ? 'status_notes.submit_title' : statusChange.next === 'APPROVED' ? 'status_notes.approve_title' : 'status_notes.back_title') : ''}
        hint={statusChange ? t(statusChange.next === 'IN_REVIEW' ? 'status_notes.submit_hint' : statusChange.next === 'APPROVED' ? 'status_notes.approve_hint' : 'status_notes.back_hint') : undefined}
        error={statusError}
        onConfirm={(notes) => statusChange && changeStatus(statusChange.row.id, statusChange.next, statusChange.row.row_version, notes)}
        onClose={() => { setStatusChange(null); setStatusError(null); }}
      />
    </PramaLayout>
  );
};

export default RegisterPage;
