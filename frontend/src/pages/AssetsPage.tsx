import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, FormControlLabel, InputLabel, MenuItem, Paper, Select, Switch, Table, TableBody,
  TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import DnsIcon from '@mui/icons-material/Dns';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { AnnotationsButton } from '../components/AnnotationsPanel';
import { useTranslation } from '../i18n/useTranslation';

interface Asset {
  id: string; row_version: number; code: string; name: string; asset_type: string; lifecycle_status: string;
  owner_unit_id: string | null; supplier_id: string | null; hosting_type: string;
  hosting_country: string | null; cloud_service: boolean; description: string | null;
  confidentiality_level: string;
}
interface Unit { id: string; name: string; code: string }
interface Party { id: string; legal_name: string; code: string }

const TYPES = ['APPLICATION','DATABASE','FILE_SHARE','PAPER_ARCHIVE','SERVER','CLOUD_SERVICE','DEVICE_CLASS','INTEGRATION','OTHER'];
const LIFE = ['PLANNED','ACTIVE','LEGACY','DISMISSION','DISMISSED'];
const HOST = ['ON_PREM','CLOUD','HYBRID','SAAS','OTHER'];

const empty = {
  name: '', asset_type: 'APPLICATION', description: '', supplier_id: '', owner_unit_id: '',
  hosting_type: 'ON_PREM', cloud_service: false, hosting_country: 'IT', confidentiality_level: 'INTERNAL',
  lifecycle_status: 'ACTIVE',
};

export const AssetsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [items, setItems] = useState<Asset[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [parties, setParties] = useState<Party[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Asset | null>(null);
  const [form, setForm] = useState(empty);

  const load = async () => {
    try {
      const [a, u, p] = await Promise.all([
        apiClient.get<Asset[]>('/api/privacypa/assets'),
        apiClient.get<Unit[]>('/api/privacypa/organizational-units'),
        apiClient.get<Party[]>('/api/privacypa/parties'),
      ]);
      setItems(a); setUnits(u); setParties(p);
    } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!form.name.trim()) return;
    const body = {
      ...form,
      supplier_id: form.supplier_id || null,
      owner_unit_id: form.owner_unit_id || null,
      hosting_country: form.hosting_country || null,
      description: form.description || null,
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
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
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
                    hosting_country: row.hosting_country || 'IT', confidentiality_level: row.confidentiality_level,
                    lifecycle_status: row.lifecycle_status,
                  });
                  setOpen(true);
                }} sx={{ cursor: 'pointer' }}>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{row.code} <AnnotationsButton entityType="asset" entityId={row.id} title={row.code} /></TableCell>
                  <TableCell>{row.name}</TableCell>
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
          <FormControl><InputLabel>{t('assets.hosting')}</InputLabel>
            <Select label={t('assets.hosting')} value={form.hosting_type} onChange={(e) => setForm({ ...form, hosting_type: e.target.value })}>
              {HOST.map((x) => <MenuItem key={x} value={x}>{t(`assets.host.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl><InputLabel>{t('assets.owner')}</InputLabel>
            <Select label={t('assets.owner')} value={form.owner_unit_id} onChange={(e) => setForm({ ...form, owner_unit_id: e.target.value })}>
              <MenuItem value="">—</MenuItem>
              {units.map((u) => <MenuItem key={u.id} value={u.id}>{u.name}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl><InputLabel>{t('assets.supplier')}</InputLabel>
            <Select label={t('assets.supplier')} value={form.supplier_id} onChange={(e) => setForm({ ...form, supplier_id: e.target.value })}>
              <MenuItem value="">—</MenuItem>
              {parties.map((p) => <MenuItem key={p.id} value={p.id}>{p.legal_name}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControlLabel control={<Switch checked={form.cloud_service} onChange={(e) => setForm({ ...form, cloud_service: e.target.checked })} />} label={t('assets.cloud')} />
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
