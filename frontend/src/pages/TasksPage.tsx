import React, { useEffect, useState } from 'react';
import {
  Alert, Box, Button, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, InputLabel, MenuItem, Paper, Select, Table, TableBody, TableCell, TableHead,
  TableRow, TextField, Typography,
} from '@mui/material';
import TaskAltIcon from '@mui/icons-material/TaskAlt';
import AddIcon from '@mui/icons-material/Add';
import { PramaLayout } from '../components/PramaLayout';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

interface Task {
  id: string; row_version: number; code: string; title: string; status: string; computed_status: string;
  priority: string; due_at: string | null; assigned_user_id: string | null; source_type: string;
}

const empty = { title: '', description: '', priority: 'MEDIUM', status: 'OPEN', due_at: '', assigned_user_id: '', source_type: 'MANUAL' };

export const TasksPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [items, setItems] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Task | null>(null);
  const [form, setForm] = useState(empty);

  const load = async () => {
    try { setItems(await apiClient.get<Task[]>('/api/privacypa/tasks')); }
    catch (e: any) { setError(e.message); }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!form.title.trim()) return;
    const body = {
      ...form,
      due_at: form.due_at ? new Date(form.due_at).toISOString() : null,
      assigned_user_id: form.assigned_user_id || null,
    };
    try {
      if (editing) await apiClient.patch(`/api/privacypa/tasks/${editing.id}`, body, { version: editing.row_version });
      else await apiClient.post('/api/privacypa/tasks', body);
      setOpen(false); await load();
    } catch (e: any) { setError(e.message); }
  };

  const color = (s: string) => s === 'OVERDUE' ? 'error' : s === 'COMPLETED' ? 'success' : 'default';

  return (
    <PramaLayout pageTitle={t('tasks.title')}>
      <Container maxWidth="lg">
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
          <TaskAltIcon color="primary" />
          <Typography variant="h4" fontWeight={600}>{t('tasks.title')}</Typography>
          <Box sx={{ flex: 1 }} />
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => { setEditing(null); setForm(empty); setOpen(true); }}>{t('tasks.add')}</Button>
        </Box>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{t('tasks.subtitle')}</Typography>
        {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}
        <Paper>
          <Table size="small">
            <TableHead><TableRow>
              <TableCell>{t('tasks.code')}</TableCell>
              <TableCell>{t('tasks.task_title')}</TableCell>
              <TableCell>{t('tasks.status')}</TableCell>
              <TableCell>{t('tasks.priority')}</TableCell>
              <TableCell>{t('tasks.due')}</TableCell>
            </TableRow></TableHead>
            <TableBody>
              {items.map((row) => (
                <TableRow key={row.id} hover sx={{ cursor: 'pointer' }} onClick={() => {
                  setEditing(row);
                  setForm({
                    title: row.title, description: '', priority: row.priority, status: row.status,
                    due_at: row.due_at ? row.due_at.slice(0, 16) : '', assigned_user_id: row.assigned_user_id || '',
                    source_type: row.source_type,
                  });
                  setOpen(true);
                }}>
                  <TableCell sx={{ fontFamily: 'monospace' }}>{row.code}</TableCell>
                  <TableCell>{row.title}</TableCell>
                  <TableCell><Chip size="small" color={color(row.computed_status) as any} label={t(`tasks.st.${row.computed_status}`)} /></TableCell>
                  <TableCell>{t(`tasks.pr.${row.priority}`)}</TableCell>
                  <TableCell>{row.due_at ? row.due_at.replace('T', ' ').slice(0, 16) : '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Paper>
      </Container>
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? editing.code : t('tasks.add')}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField label={t('tasks.task_title')} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required />
          <TextField label={t('tasks.description')} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} multiline minRows={2} />
          <FormControl><InputLabel>{t('tasks.priority')}</InputLabel>
            <Select label={t('tasks.priority')} value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
              {['LOW','MEDIUM','HIGH','CRITICAL'].map((x) => <MenuItem key={x} value={x}>{t(`tasks.pr.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <FormControl><InputLabel>{t('tasks.status')}</InputLabel>
            <Select label={t('tasks.status')} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
              {['OPEN','IN_PROGRESS','WAITING','COMPLETED','CANCELLED'].map((x) => <MenuItem key={x} value={x}>{t(`tasks.st.${x}`)}</MenuItem>)}
            </Select>
          </FormControl>
          <TextField type="datetime-local" label={t('tasks.due')} InputLabelProps={{ shrink: true }} value={form.due_at} onChange={(e) => setForm({ ...form, due_at: e.target.value })} />
          <TextField label={t('tasks.assignee')} value={form.assigned_user_id} onChange={(e) => setForm({ ...form, assigned_user_id: e.target.value })} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={save}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </PramaLayout>
  );
};
export default TasksPage;
