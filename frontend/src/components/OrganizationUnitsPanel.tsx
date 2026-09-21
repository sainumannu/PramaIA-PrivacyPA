import React, { useEffect, useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  MenuItem,
  Alert,
  Chip,
  Tooltip,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import EditIcon from '@mui/icons-material/Edit';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

interface OrganizationalUnit {
  id: string;
  row_version: number;
  tenant_id: string;
  organization_id: string;
  parent_id: string | null;
  code: string;
  name: string;
  unit_type: string;
  manager_user_id: string | null;
  active: boolean;
}

const UNIT_TYPES = ['AREA', 'SETTORE', 'SERVIZIO', 'UFFICIO', 'UNITA', 'COMANDO', 'ISTITUZIONE'];

const emptyForm = {
  code: '',
  name: '',
  unit_type: 'AREA',
  parent_id: '',
  manager_user_id: '',
};

export const OrganizationUnitsPanel: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [units, setUnits] = useState<OrganizationalUnit[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingUnit, setEditingUnit] = useState<OrganizationalUnit | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = async () => {
    try {
      const data = await apiClient.get<OrganizationalUnit[]>('/api/privacypa/organizational-units');
      setUnits(data);
    } catch (err: any) {
      setError(err.message || 'Errore di caricamento strutture');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const unitName = (id: string | null) => units.find((u) => u.id === id)?.name || '—';

  const typeLabel = (type: string) =>
    t(`organization.units.unit_types.${type}`) || type;

  const openCreate = () => {
    setEditingUnit(null);
    setForm(emptyForm);
    setDialogOpen(true);
  };

  const openEdit = (unit: OrganizationalUnit) => {
    setEditingUnit(unit);
    setForm({
      code: unit.code,
      name: unit.name,
      unit_type: unit.unit_type,
      parent_id: unit.parent_id || '',
      manager_user_id: unit.manager_user_id || '',
    });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.code.trim() || !form.name.trim()) {
      setError(t('organization.units.fields_required') || 'Codice e denominazione sono obbligatori');
      return;
    }
    try {
      const payload = {
        code: form.code,
        name: form.name,
        unit_type: form.unit_type,
        parent_id: form.parent_id || null,
        manager_user_id: form.manager_user_id || null,
      };
      if (editingUnit) {
        await apiClient.patch(`/api/privacypa/organizational-units/${editingUnit.id}`, payload, { version: editingUnit.row_version });
        setSuccess(t('organization.units.updated') || 'Struttura aggiornata');
      } else {
        await apiClient.post('/api/privacypa/organizational-units', payload);
        setSuccess(t('organization.units.created') || 'Struttura creata');
      }
      setDialogOpen(false);
      load();
    } catch (err: any) {
      setError(err.message || t('organization.units.save_failed') || 'Salvataggio fallito');
    }
  };

  return (
    <Paper sx={{ p: 3 }}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}
      {success && (
        <Alert severity="success" onClose={() => setSuccess(null)} sx={{ mb: 2 }}>
          {success}
        </Alert>
      )}

      <Box sx={{ mb: 2, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Typography variant="h6">
          {t('organization.units.title') || 'Strutture organizzative'}
        </Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={openCreate}>
          {t('organization.units.add') || 'Nuova struttura'}
        </Button>
      </Box>

      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t('organization.units.code') || 'Codice'}</TableCell>
              <TableCell>{t('organization.units.name') || 'Denominazione'}</TableCell>
              <TableCell>{t('organization.units.type') || 'Tipo'}</TableCell>
              <TableCell>{t('organization.units.parent') || 'Struttura padre'}</TableCell>
              <TableCell>{t('organization.units.manager') || 'Responsabile'}</TableCell>
              <TableCell align="right">{t('common.actions') || 'Azioni'}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {units.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} align="center">
                  {t('organization.units.no_units') || 'Nessuna struttura organizzativa censita'}
                </TableCell>
              </TableRow>
            ) : (
              units.map((unit) => (
                <TableRow key={unit.id}>
                  <TableCell><code>{unit.code}</code></TableCell>
                  <TableCell>{unit.name}</TableCell>
                  <TableCell><Chip size="small" label={typeLabel(unit.unit_type)} /></TableCell>
                  <TableCell>{unitName(unit.parent_id)}</TableCell>
                  <TableCell>{unit.manager_user_id || '—'}</TableCell>
                  <TableCell align="right">
                    <Tooltip title={t('common.edit') || 'Modifica'}>
                      <IconButton size="small" onClick={() => openEdit(unit)}>
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>
          {editingUnit
            ? t('organization.units.edit') || 'Modifica struttura'
            : t('organization.units.add') || 'Nuova struttura'}
        </DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            <TextField
              label={t('organization.units.code') || 'Codice'}
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
              fullWidth
              required
            />
            <TextField
              label={t('organization.units.name') || 'Denominazione'}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              fullWidth
              required
            />
            <TextField
              select
              label={t('organization.units.type') || 'Tipo'}
              value={form.unit_type}
              onChange={(e) => setForm({ ...form, unit_type: e.target.value })}
              fullWidth
            >
              {UNIT_TYPES.map((type) => (
                <MenuItem key={type} value={type}>
                  {typeLabel(type)}
                </MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label={t('organization.units.parent') || 'Struttura padre'}
              value={form.parent_id}
              onChange={(e) => setForm({ ...form, parent_id: e.target.value })}
              fullWidth
            >
              <MenuItem value="">{t('organization.units.none') || '— nessuna (radice) —'}</MenuItem>
              {units
                .filter((u) => u.id !== editingUnit?.id)
                .map((u) => (
                  <MenuItem key={u.id} value={u.id}>
                    {u.name} ({u.code})
                  </MenuItem>
                ))}
            </TextField>
            <TextField
              label={t('organization.units.manager') || 'Responsabile (user id)'}
              value={form.manager_user_id}
              onChange={(e) => setForm({ ...form, manager_user_id: e.target.value })}
              fullWidth
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>{t('common.cancel') || 'Annulla'}</Button>
          <Button variant="contained" onClick={handleSave}>
            {t('common.save') || 'Salva'}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
};

export default OrganizationUnitsPanel;
