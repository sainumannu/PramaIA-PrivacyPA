import React, { useEffect, useState } from 'react';
import {
  Box, Button, Checkbox, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle, Divider,
  FormControl, FormControlLabel, FormGroup, InputLabel, MenuItem, Paper, Select, Switch, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import DnsIcon from '@mui/icons-material/Dns';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { AnnotationsButton } from '../components/AnnotationsPanel';
import { PersonField, usePortalPeople } from '../components/PortalPeople';
import { UnitPicker } from '../components/UnitPicker';
import { PartyRef, PartySelect } from '../components/PartyPicker';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';

interface Asset {
  id: string; row_version: number; code: string; name: string; asset_type: string; lifecycle_status: string;
  owner_unit_id: string | null; supplier_id: string | null; hosting_type: string;
  hosting_country: string | null; cloud_service: boolean; description: string | null;
  confidentiality_level: string;
  data_owner_name: string | null; data_owner_role: string | null; data_owner_user_id: string | null;
  location_site: string | null; location_room: string | null; archive_phase: string | null; archive_extent: string | null;
  custodian_user_id: string | null; environmental_risks: string[]; physical_measure_ids: string[];
  [field: string]: unknown;
}
interface Unit { id: string; name: string; code: string }
interface Measure { id: string; name: string; is_global: boolean }

const TYPES = ['APPLICATION','DATABASE','FILE_SHARE','PAPER_ARCHIVE','SERVER','CLOUD_SERVICE','DEVICE_CLASS','INTEGRATION','OTHER'];
const LIFE = ['PLANNED','ACTIVE','LEGACY','DISMISSION','DISMISSED'];
const HOST = ['ON_PREM','CLOUD','HYBRID','SAAS','OTHER'];
const PHASES = ['CURRENT', 'DEPOSIT', 'HISTORICAL'];
const RISKS = ['FIRE', 'FLOOD', 'HUMIDITY', 'PESTS', 'INTRUSION'];
const PAPER = 'PAPER_ARCHIVE';
// Campi del sistema che il modulo non mostra: il PATCH sostituisce il record, quindi in modifica si rimandano com'erano.
const NOT_IN_FORM = [
  'data_location_notes', 'authentication_type', 'mfa_supported', 'logging_supported', 'encryption_at_rest',
  'encryption_in_transit', 'backup_enabled', 'go_live_date', 'retirement_date',
];

const empty = {
  name: '', asset_type: 'APPLICATION', description: '', supplier_id: '', owner_unit_id: '',
  hosting_type: 'ON_PREM', cloud_service: false, hosting_country: 'IT', confidentiality_level: 'INTERNAL',
  lifecycle_status: 'ACTIVE', data_owner_name: '', data_owner_role: '', data_owner_user_id: '',
  location_site: '', location_room: '', archive_phase: '', archive_extent: '', custodian_user_id: '',
  environmental_risks: [] as string[], physical_measure_ids: [] as string[],
};
const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

export const AssetsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [items, setItems] = useState<Asset[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [parties, setParties] = useState<PartyRef[]>([]);
  const [measures, setMeasures] = useState<Measure[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Asset | null>(null);
  const [form, setForm] = useState(empty);
  const people = usePortalPeople();

  const load = async () => {
    try {
      const [a, u, p] = await Promise.all([
        apiClient.get<Asset[]>('/api/privacypa/assets'),
        apiClient.get<Unit[]>('/api/privacypa/organizational-units'),
        apiClient.get<PartyRef[]>('/api/privacypa/parties'),
      ]);
      setItems(a); setUnits(u); setParties(p);
    } catch (e: any) { setError(e.message); }
    apiClient.get<Measure[]>('/api/privacypa/assets/physical-measures').then(setMeasures).catch(() => setMeasures([]));
  };
  const paper = form.asset_type === PAPER;
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!form.name.trim()) { setError(t('common.fill_required')); return; }
    const kept = editing ? Object.fromEntries(NOT_IN_FORM.map((f) => [f, editing[f] ?? null])) : {};
    const body = {
      ...kept,
      ...form,
      supplier_id: form.supplier_id || null,
      owner_unit_id: form.owner_unit_id || null,
      hosting_country: form.hosting_country || null,
      description: form.description || null,
      data_owner_name: form.data_owner_name.trim() || null,
      data_owner_role: form.data_owner_role.trim() || null,
      data_owner_user_id: form.data_owner_user_id.trim() || null,
      // per gli altri tipi il backend azzera i campi dell'archivio; le misure fisiche si toccano solo da qui
      location_site: form.location_site.trim() || null,
      location_room: form.location_room.trim() || null,
      archive_phase: form.archive_phase || null,
      archive_extent: form.archive_extent.trim() || null,
      custodian_user_id: form.custodian_user_id || null,
      physical_measure_ids: paper ? form.physical_measure_ids : editing?.physical_measure_ids ?? null,
    };
    try {
      if (editing) await apiClient.patch(`/api/privacypa/assets/${editing.id}`, body, { version: editing.row_version });
      else await apiClient.post('/api/privacypa/assets', body);
      setOpen(false); await load();
    } catch (e: any) { setError(e.message); }
  };

  return (
    <PramaLayout pageTitle={t('assets.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <DnsIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('assets.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => { setEditing(null); setForm(empty); setOpen(true); }}>{t('assets.add')}</Button>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('assets.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('assets.code')}</TableCell>
                <TableCell>{t('assets.name')}</TableCell>
                <TableCell>{t('assets.type')}</TableCell>
                <TableCell>{t('assets.lifecycle')}</TableCell>
                <TableCell>{t('assets.owner')}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {items.length === 0 && <TableRow><TableCell colSpan={5}>{t('assets.empty')}</TableCell></TableRow>}
              {items.map((row) => (
                <TableRow key={row.id} hover onClick={() => {
                  setEditing(row);
                  setForm({
                    name: row.name, asset_type: row.asset_type, description: row.description || '',
                    supplier_id: row.supplier_id || '', owner_unit_id: row.owner_unit_id || '',
                    hosting_type: row.hosting_type, cloud_service: row.cloud_service,
                    hosting_country: row.hosting_country || '', confidentiality_level: row.confidentiality_level,
                    lifecycle_status: row.lifecycle_status, data_owner_name: row.data_owner_name || '',
                    data_owner_role: row.data_owner_role || '', data_owner_user_id: row.data_owner_user_id || '',
                    location_site: row.location_site || '', location_room: row.location_room || '',
                    archive_phase: row.archive_phase || '', archive_extent: row.archive_extent || '',
                    custodian_user_id: row.custodian_user_id || '', environmental_risks: row.environmental_risks || [],
                    physical_measure_ids: row.physical_measure_ids || [],
                  });
                  setOpen(true);
                }} sx={{ cursor: 'pointer' }}>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{row.code} <AnnotationsButton entityType="asset" entityId={row.id} title={row.code} /></TableCell>
                  <TableCell>
                    {row.name}
                    {row.asset_type === PAPER && (row.location_site || row.location_room) && (
                      <Typography variant="caption" display="block" color="text.secondary">
                        {[row.location_site, row.location_room].filter(Boolean).join(' · ')}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>{t(`assets.types.${row.asset_type}`)}</TableCell>
                  <TableCell><Chip size="small" label={t(`assets.life.${row.lifecycle_status}`)} /></TableCell>
                  <TableCell>{units.find((u) => u.id === row.owner_unit_id)?.name || '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? editing.code : t('assets.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label={t('assets.name')} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <FormControl><InputLabel>{t('assets.type')}</InputLabel>
            <Select label={t('assets.type')} value={form.asset_type} onChange={(e) => setForm({ ...form, asset_type: e.target.value })}>
              {TYPES.map((x) => <MenuItem key={x} value={x}>{t(`assets.types.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl><InputLabel>{t('assets.lifecycle')}</InputLabel>
            <Select label={t('assets.lifecycle')} value={form.lifecycle_status} onChange={(e) => setForm({ ...form, lifecycle_status: e.target.value })}>
              {LIFE.map((x) => <MenuItem key={x} value={x}>{t(`assets.life.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          {!paper && (
            <FormControl><InputLabel>{t('assets.hosting')}</InputLabel>
              <Select label={t('assets.hosting')} value={form.hosting_type} onChange={(e) => setForm({ ...form, hosting_type: e.target.value })}>
                {HOST.map((x) => <MenuItem key={x} value={x}>{t(`assets.host.${x}`)}</MenuItem>)}
              </Select>
            </FormControl>
          )}
          <UnitPicker
            label={t('assets.owner')} value={form.owner_unit_id}
            onChange={(id) => setForm((f) => ({ ...f, owner_unit_id: id }))}
            onUnitAdded={() => apiClient.get<Unit[]>('/api/privacypa/organizational-units').then(setUnits).catch(() => undefined)}
          />
          {paper && (
            <>
              <Divider />
              <Typography variant="subtitle2">{t('assets.paper.title')}</Typography>
              <Typography variant="caption" color="text.secondary" sx={{ mt: -1.5 }}>{t('assets.paper.hint')}</Typography>
              <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
                <TextField label={t('assets.paper.site')} value={form.location_site} onChange={(e) => setForm({ ...form, location_site: e.target.value })} />
                <TextField label={t('assets.paper.room')} value={form.location_room} onChange={(e) => setForm({ ...form, location_room: e.target.value })} />
                <FormControl><InputLabel>{t('assets.paper.phase')}</InputLabel>
                  <Select label={t('assets.paper.phase')} value={form.archive_phase} onChange={(e) => setForm({ ...form, archive_phase: e.target.value })}
                    renderValue={(v) => (v ? t(`assets.paper.phases.${v}`) : '')}>
                    <MenuItem value=""><em>{t('assets.paper.not_set')}</em></MenuItem>
                    {PHASES.map((x) => (
                      <MenuItem key={x} value={x} sx={{ display: 'block' }}>
                        {t(`assets.paper.phases.${x}`)}
                        <Typography variant="caption" display="block" color="text.secondary">{t(`assets.paper.phase_hint.${x}`)}</Typography>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
                <TextField label={t('assets.paper.extent')} placeholder={t('assets.paper.extent_hint')} value={form.archive_extent}
                  onChange={(e) => setForm({ ...form, archive_extent: e.target.value })} />
              </Box>
              <PersonField
                label={t('assets.paper.custodian')} value={form.custodian_user_id} hint={t('assets.paper.custodian_hint')}
                onChange={(v) => setForm((f) => ({ ...f, custodian_user_id: v }))}
              />
              <Box>
                <Typography variant="body2" color="text.secondary">{t('assets.paper.risks')}</Typography>
                <FormGroup row>
                  {RISKS.map((x) => (
                    <FormControlLabel key={x} label={t(`assets.paper.risk.${x}`)} control={
                      <Checkbox size="small" checked={form.environmental_risks.includes(x)}
                        onChange={() => setForm((f) => ({ ...f, environmental_risks: toggle(f.environmental_risks, x) }))} />
                    } />
                  ))}
                </FormGroup>
              </Box>
              <Box>
                <Typography variant="body2" color="text.secondary">{t('assets.paper.measures')}</Typography>
                <Typography variant="caption" color="text.secondary">{t('assets.paper.measures_hint')}</Typography>
                <FormGroup>
                  {measures.map((m) => (
                    <FormControlLabel key={m.id} label={m.name} control={
                      <Checkbox size="small" checked={form.physical_measure_ids.includes(m.id)}
                        onChange={() => setForm((f) => ({ ...f, physical_measure_ids: toggle(f.physical_measure_ids, m.id) }))} />
                    } />
                  ))}
                </FormGroup>
              </Box>
              <Divider />
            </>
          )}
          <PartySelect
            label={t(paper ? 'assets.paper.external_custodian' : 'assets.supplier')} parties={parties} defaultType="SUPPLIER"
            value={form.supplier_id} onChange={(id) => setForm((f) => ({ ...f, supplier_id: id }))}
            onPartyAdded={(p) => setParties((list) => [...list, p])}
          />
          <Typography variant="subtitle2" sx={{ mt: 1 }}>{t('assets.data_owner')}</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ mt: -1.5 }}>{t('assets.data_owner_hint')}</Typography>
          <TextField label={t('assets.data_owner_name')} value={form.data_owner_name} onChange={(e) => setForm({ ...form, data_owner_name: e.target.value })} />
          <TextField label={t('assets.data_owner_role')} value={form.data_owner_role} onChange={(e) => setForm({ ...form, data_owner_role: e.target.value })} />
          <PersonField
            label={t('assets.data_owner_user_id')} value={form.data_owner_user_id}
            // Scelta una persona del Portal, il nome si compila da solo se è vuoto
            onChange={(v) => setForm((f) => ({ ...f, data_owner_user_id: v, data_owner_name: f.data_owner_name || people.byId[v]?.name || '' }))}
          />
          {!paper && <FormControlLabel control={<Switch checked={form.cloud_service} onChange={(e) => setForm({ ...form, cloud_service: e.target.checked })} />} label={t('assets.cloud')} />}
          <TextField label={t('assets.description')} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} multiline minRows={2} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={save}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </PramaLayout>
  );
};
export default AssetsPage;
