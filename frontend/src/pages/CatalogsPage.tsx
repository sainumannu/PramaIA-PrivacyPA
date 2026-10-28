import React, { useEffect, useState } from 'react';
import {
  Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, InputLabel, MenuItem, Paper, Select, Tab, Table, TableBody, TableCell, TableHead,
  TableRow, Tabs, TextField, Typography,
} from '@mui/material';
import MenuBookIcon from '@mui/icons-material/MenuBook';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from '../components/ErrorBanner';

interface LegalBasis { code: string; article: string; paragraph: string | null; letter: string | null; description: string }
interface Subject { id: string; tenant_id: string | null; code: string; name: string; description: string | null }
interface DataCat { id: string; tenant_id: string | null; parent_id: string | null; code: string; name: string; gdpr_class: string }

export const CatalogsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [tab, setTab] = useState(0);
  const [bases, setBases] = useState<LegalBasis[]>([]);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [cats, setCats] = useState<DataCat[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ code: '', name: '', description: '', article: '', gdpr_class: 'ORDINARY', parent_id: '' });

  const load = async () => {
    try {
      const [b, s, c] = await Promise.all([
        apiClient.get<LegalBasis[]>('/api/privacypa/catalogs/legal-basis'),
        apiClient.get<Subject[]>('/api/privacypa/catalogs/data-subject-categories'),
        apiClient.get<DataCat[]>('/api/privacypa/catalogs/data-categories'),
      ]);
      setBases(b); setSubjects(s); setCats(c);
    } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  const isLocal = (tenantId: string | null) => Boolean(tenantId);

  const save = async () => {
    try {
      if (tab === 0) {
        await apiClient.post('/api/privacypa/catalogs/legal-basis', {
          code: form.code, article: form.article || '6', description: form.description || form.name || form.code,
        });
      } else if (tab === 1) {
        await apiClient.post('/api/privacypa/catalogs/data-subject-categories', {
          code: form.code, name: form.name, description: form.description || null,
        });
      } else {
        await apiClient.post('/api/privacypa/catalogs/data-categories', {
          code: form.code, name: form.name, gdpr_class: form.gdpr_class,
          parent_id: form.parent_id || null, description: form.description || null,
        });
      }
      setOpen(false);
      setForm({ code: '', name: '', description: '', article: '', gdpr_class: 'ORDINARY', parent_id: '' });
      await load();
    } catch (e: any) { setError(e.message); }
  };

  const removeSubject = async (row: Subject) => {
    if (!isLocal(row.tenant_id)) return;
    try { await apiClient.delete(`/api/privacypa/catalogs/data-subject-categories/${row.id}`); await load(); }
    catch (e: any) { setError(e.message); }
  };
  const removeCat = async (row: DataCat) => {
    if (!isLocal(row.tenant_id)) return;
    try { await apiClient.delete(`/api/privacypa/catalogs/data-categories/${row.id}`); await load(); }
    catch (e: any) { setError(e.message); }
  };

  return (
    <PramaLayout pageTitle={t('catalogsPage.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <MenuBookIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('catalogsPage.title')}</Typography>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('catalogsPage.subtitle')}</Typography>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <Paper>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ borderBottom: 1, borderColor: 'divider', '& .MuiTab-root': { textTransform: 'none' } }}>
            <Tab label={t('catalogs.legal_basis')} />
            <Tab label={t('catalogs.subjects')} />
            <Tab label={t('catalogs.data_categories')} />
          </Tabs>
          <Box sx={{ p: 2 }}>
            <Button variant="contained" startIcon={<AddIcon />} sx={{ mb: 2 }} onClick={() => setOpen(true)}>
              {t('catalogsPage.add')}
            </Button>
            {tab === 0 && (
              <Table size="small">
                <TableHead><TableRow><TableCell>Code</TableCell><TableCell>{t('catalogsPage.description')}</TableCell></TableRow></TableHead>
                <TableBody>
                  {bases.map((b) => (
                    <TableRow key={b.code}><TableCell sx={{ fontFamily: 'monospace' }}>{b.code}</TableCell><TableCell>{b.description}</TableCell></TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {tab === 1 && (
              <Table size="small">
                <TableHead><TableRow>
                  <TableCell>Code</TableCell><TableCell>{t('catalogsPage.name')}</TableCell>
                  <TableCell>{t('catalogsPage.origin')}</TableCell><TableCell />
                </TableRow></TableHead>
                <TableBody>
                  {subjects.map((s) => (
                    <TableRow key={s.id}>
                      <TableCell sx={{ fontFamily: 'monospace' }}>{s.code}</TableCell>
                      <TableCell>{s.name}</TableCell>
                      <TableCell><Chip size="small" label={isLocal(s.tenant_id) ? t('catalogsPage.local') : t('catalogsPage.global')} /></TableCell>
                      <TableCell>{isLocal(s.tenant_id) && <Button size="small" color="error" onClick={() => removeSubject(s)}>{t('catalogsPage.delete')}</Button>}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            {tab === 2 && (
              <Table size="small">
                <TableHead><TableRow>
                  <TableCell>Code</TableCell><TableCell>{t('catalogsPage.name')}</TableCell>
                  <TableCell>GDPR</TableCell><TableCell>{t('catalogsPage.origin')}</TableCell><TableCell />
                </TableRow></TableHead>
                <TableBody>
                  {cats.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell sx={{ fontFamily: 'monospace' }}>{c.code}</TableCell>
                      <TableCell>{c.name}</TableCell>
                      <TableCell>{c.gdpr_class}</TableCell>
                      <TableCell><Chip size="small" label={isLocal(c.tenant_id) ? t('catalogsPage.local') : t('catalogsPage.global')} /></TableCell>
                      <TableCell>{isLocal(c.tenant_id) && <Button size="small" color="error" onClick={() => removeCat(c)}>{t('catalogsPage.delete')}</Button>}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Box>
        </Paper>
      </Container>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('catalogsPage.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label="Code" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
          {tab === 0 && <TextField label="Articolo" value={form.article} onChange={(e) => setForm({ ...form, article: e.target.value })} />}
          {tab !== 0 && <TextField label={t('catalogsPage.name')} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />}
          <TextField label={t('catalogsPage.description')} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          {tab === 2 && (
            <FormControl>
              <InputLabel>GDPR</InputLabel>
              <Select label="GDPR" value={form.gdpr_class} onChange={(e) => setForm({ ...form, gdpr_class: e.target.value })}>
                <MenuItem value="ORDINARY">ORDINARY</MenuItem>
                <MenuItem value="SPECIAL_ART9">SPECIAL_ART9</MenuItem>
                <MenuItem value="CRIMINAL_ART10">CRIMINAL_ART10</MenuItem>
              </Select>
            </FormControl>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={save}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </PramaLayout>
  );
};
export default CatalogsPage;
