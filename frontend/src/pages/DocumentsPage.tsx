import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, InputLabel, MenuItem, Paper, Select, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from '@mui/material';
import DescriptionIcon from '@mui/icons-material/Description';
import AddIcon from '@mui/icons-material/Add';
import UploadIcon from '@mui/icons-material/Upload';
import DownloadIcon from '@mui/icons-material/Download';
import RefreshIcon from '@mui/icons-material/Refresh';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';

interface Doc {
  id: string; code: string; title: string; document_type: string; status: string;
  current_version?: { id: string; file_name: string; sha256: string; expiry_date: string | null; version_no: number; scan_status: string };
}

const TYPES = ['POLICY','AGREEMENT_ART28','NOTICE','DPIA','EVIDENCE','REGISTER_EXTRACT','OTHER'];

const LEVELS = ['INTERNAL', 'CONFIDENTIAL', 'RESTRICTED', 'HIGHLY_RESTRICTED'];

export const DocumentsPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const { can } = usePermissions();
  const [items, setItems] = useState<Doc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState('');
  const [docType, setDocType] = useState('OTHER');
  const [level, setLevel] = useState('INTERNAL');
  const [uploadFor, setUploadFor] = useState<Doc | null>(null);
  const [file, setFile] = useState<File | null>(null);

  const load = async () => {
    try { setItems(await apiClient.get<Doc[]>('/api/privacypa/documents')); }
    catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!title.trim()) return;
    try {
      await apiClient.post('/api/privacypa/documents', { title, document_type: docType, confidentiality_level: level });
      setOpen(false); setTitle(''); await load();
    } catch (e: any) { setError(e.message); }
  };

  const upload = async () => {
    if (!uploadFor || !file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      await apiClient.upload(`/api/privacypa/documents/${uploadFor.id}/versions`, form);
      setUploadFor(null); setFile(null); await load();
    } catch (e: any) { setError(e.message); }
  };

  return (
    <PramaLayout pageTitle={t('documents.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <DescriptionIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('documents.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setOpen(true)}>{t('documents.add')}</Button>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('documents.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        <Paper>
          <Table size="small">
            <TableHead><TableRow>
              <TableCell>{t('documents.code')}</TableCell>
              <TableCell>{t('documents.doc_title')}</TableCell>
              <TableCell>{t('documents.type')}</TableCell>
              <TableCell>{t('documents.status')}</TableCell>
              <TableCell>{t('documents.file')}</TableCell>
              <TableCell />
            </TableRow></TableHead>
            <TableBody>
              {items.map((row) => (
                <TableRow key={row.id}>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{row.code}</TableCell>
                  <TableCell>{row.title}</TableCell>
                  <TableCell>
                    {t(`documents.types.${row.document_type}`)}
                    {(row as any).confidentiality_level && !['INTERNAL', 'PUBLIC'].includes((row as any).confidentiality_level) && (
                      <Chip size="small" color="warning" sx={{ ml: 1 }} label={t(`register.confidentiality.${(row as any).confidentiality_level}`)} />
                    )}
                  </TableCell>
                  <TableCell><Chip size="small" label={t(`documents.st.${row.status}`)} /></TableCell>
                  <TableCell>
                    {row.current_version ? `v${row.current_version.version_no} ${row.current_version.file_name}` : '—'}
                    {row.current_version && row.current_version.scan_status !== 'CLEAN' && (
                      <Chip
                        size="small"
                        color={row.current_version.scan_status === 'INFECTED' ? 'error' : 'warning'}
                        sx={{ ml: 1 }}
                        label={t(`documents.scan.${row.current_version.scan_status}`)}
                      />
                    )}
                  </TableCell>
                  <TableCell align="right">
                    <Button size="small" startIcon={<UploadIcon />} onClick={() => setUploadFor(row)}>{t('documents.upload')}</Button>
                    {row.current_version && row.current_version.scan_status !== 'CLEAN' && can('document.administer') && (
                      <Button
                        size="small"
                        startIcon={<RefreshIcon />}
                        onClick={async () => {
                          try {
                            await apiClient.post(`/api/privacypa/documents/${row.id}/versions/${row.current_version!.id}/rescan`, {});
                            await load();
                          } catch (e: any) { setError(e.message); }
                        }}
                      >
                        {t('documents.scan.rescan')}
                      </Button>
                    )}
                    {row.current_version && (
                      <Button size="small" startIcon={<DownloadIcon />} disabled={row.current_version.scan_status !== 'CLEAN'} onClick={() => apiClient.download(`/api/privacypa/documents/${row.id}/file`, row.current_version!.file_name)}>{t('documents.download')}</Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>
      <Dialog open={open} onClose={() => setOpen(false)}>
        <DialogTitle>{t('documents.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2, minWidth: 360 }}>
          <TextField label={t('documents.doc_title')} value={title} onChange={(e) => setTitle(e.target.value)} />
          <FormControl><InputLabel>{t('documents.type')}</InputLabel>
            <Select label={t('documents.type')} value={docType} onChange={(e) => setDocType(e.target.value)}>
              {TYPES.map((x) => <MenuItem key={x} value={x}>{t(`documents.types.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl><InputLabel>{t('register.fields.confidentiality')}</InputLabel>
            <Select label={t('register.fields.confidentiality')} value={level} onChange={(e) => setLevel(e.target.value)}>
              {LEVELS.map((x) => <MenuItem key={x} value={x}>{t(`register.confidentiality.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={create}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
      <Dialog open={Boolean(uploadFor)} onClose={() => setUploadFor(null)}>
        <DialogTitle>{t('documents.upload')}</DialogTitle>
        <DialogContent>
          <Button variant="outlined" component="label">{t('documents.choose_file')}
            <input hidden type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </Button>
          {file && <Typography variant="body2" sx={{ mt: 1 }}>{file.name}</Typography>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setUploadFor(null)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={upload} disabled={!file}>{t('documents.upload')}</Button>
        </DialogActions>
      </Dialog>
    </PramaLayout>
  );
};
export default DocumentsPage;
