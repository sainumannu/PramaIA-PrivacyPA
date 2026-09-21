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
  TextField,
  MenuItem,
  Alert,
  Chip,
  Tooltip,
  Grid,
  Divider,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

interface PrivacyRole {
  code: string;
  name: string;
  description: string | null;
}

interface OrganizationalUnit {
  id: string;
  name: string;
  code: string;
}

interface UserRoleAssignment {
  id: string;
  user_id: string;
  role_code: string;
  organizational_unit_id: string | null;
  scope_type: string;
  valid_to: string | null;
  granted_by: string | null;
  granted_at: string | null;
}

const SCOPE_TYPES = ['TENANT', 'UNIT', 'SUBTREE', 'RECORD'];

export const PrivacyRolesPanel: React.FC = () => {
  const { t } = useTranslation('privacypa');

  const [catalog, setCatalog] = useState<PrivacyRole[]>([]);
  const [units, setUnits] = useState<OrganizationalUnit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const [userId, setUserId] = useState('');
  const [assignments, setAssignments] = useState<UserRoleAssignment[]>([]);

  const [roleCode, setRoleCode] = useState('');
  const [scopeType, setScopeType] = useState('TENANT');
  const [unitId, setUnitId] = useState('');

  const loadCatalog = async () => {
    try {
      const data = await apiClient.get<PrivacyRole[]>('/api/privacypa/roles/catalog');
      setCatalog(data);
    } catch (err: any) {
      setError(err.message || 'Errore caricamento catalogo ruoli');
    }
  };

  const loadUnits = async () => {
    try {
      const data = await apiClient.get<OrganizationalUnit[]>('/api/privacypa/organizational-units');
      setUnits(data);
    } catch {
      // silenzioso: la selezione struttura è opzionale finché non serve
    }
  };

  useEffect(() => {
    loadCatalog();
    loadUnits();
  }, []);

  const loadAssignments = async (id: string) => {
    if (!id) {
      setAssignments([]);
      return;
    }
    try {
      const data = await apiClient.get<UserRoleAssignment[]>(`/api/privacypa/users/${id}/roles`);
      setAssignments(data);
    } catch (err: any) {
      setError(err.message || 'Errore caricamento ruoli utente');
    }
  };

  const handleUserIdBlur = () => {
    loadAssignments(userId);
  };

  const roleName = (code: string) => catalog.find((r) => r.code === code)?.name || code;
  const unitName = (id: string | null) => units.find((u) => u.id === id)?.name || '—';

  const handleAssign = async () => {
    if (!userId.trim() || !roleCode) {
      setError(t('organization.roles.select_user_and_role') || 'Indicare utente e ruolo');
      return;
    }
    if ((scopeType === 'UNIT' || scopeType === 'SUBTREE') && !unitId) {
      setError(t('organization.roles.unit_required') || 'Selezionare una struttura per questo ambito');
      return;
    }
    try {
      await apiClient.post('/api/privacypa/roles/assign', {
        user_id: userId,
        role_code: roleCode,
        scope_type: scopeType,
        organizational_unit_id: scopeType === 'TENANT' || scopeType === 'RECORD' ? null : unitId,
      });
      setSuccess(t('organization.roles.assigned') || 'Ruolo assegnato con successo');
      setRoleCode('');
      setScopeType('TENANT');
      setUnitId('');
      loadAssignments(userId);
    } catch (err: any) {
      setError(err.message || t('organization.roles.assign_failed') || 'Assegnazione fallita');
    }
  };

  const handleRevoke = async (assignmentId: string) => {
    if (!window.confirm(t('organization.roles.confirm_revoke') || 'Revocare questo ruolo?')) {
      return;
    }
    try {
      await apiClient.delete(`/api/privacypa/roles/${assignmentId}`);
      setSuccess(t('organization.roles.revoked') || 'Ruolo revocato');
      loadAssignments(userId);
    } catch (err: any) {
      setError(err.message || t('organization.roles.revoke_failed') || 'Revoca fallita');
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

      <Typography variant="h6" sx={{ mb: 2 }}>
        {t('organization.roles.catalog_title') || 'Catalogo ruoli privacy'}
      </Typography>
      <TableContainer sx={{ mb: 4 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t('organization.roles.code') || 'Codice'}</TableCell>
              <TableCell>{t('organization.roles.name') || 'Nome'}</TableCell>
              <TableCell>{t('organization.roles.description') || 'Descrizione'}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {catalog.map((role) => (
              <TableRow key={role.code}>
                <TableCell><code>{role.code}</code></TableCell>
                <TableCell>{role.name}</TableCell>
                <TableCell>{role.description || '—'}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>

      <Divider sx={{ mb: 3 }} />

      <Typography variant="h6" sx={{ mb: 2 }}>
        {t('organization.roles.assignments_title') || 'Assegnazioni utente'}
      </Typography>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        <Grid item xs={12} sm={4}>
          <TextField
            label={t('organization.roles.user_id') || 'ID utente (email/sub Portal)'}
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            onBlur={handleUserIdBlur}
            fullWidth
          />
        </Grid>
        <Grid item xs={12} sm={3}>
          <TextField
            select
            label={t('organization.roles.role') || 'Ruolo'}
            value={roleCode}
            onChange={(e) => setRoleCode(e.target.value)}
            fullWidth
          >
            <MenuItem value="">—</MenuItem>
            {catalog.map((role) => (
              <MenuItem key={role.code} value={role.code}>
                {role.name}
              </MenuItem>
            ))}
          </TextField>
        </Grid>
        <Grid item xs={12} sm={2}>
          <TextField
            select
            label={t('organization.roles.scope_type') || 'Ambito'}
            value={scopeType}
            onChange={(e) => setScopeType(e.target.value)}
            fullWidth
          >
            {SCOPE_TYPES.map((s) => (
              <MenuItem key={s} value={s}>
                {t(`organization.roles.scope.${s}`) || s}
              </MenuItem>
            ))}
          </TextField>
        </Grid>
        <Grid item xs={12} sm={3}>
          <TextField
            select
            label={t('organization.roles.scope_unit') || 'Struttura di riferimento'}
            value={unitId}
            onChange={(e) => setUnitId(e.target.value)}
            fullWidth
            disabled={scopeType !== 'UNIT' && scopeType !== 'SUBTREE'}
          >
            <MenuItem value="">—</MenuItem>
            {units.map((u) => (
              <MenuItem key={u.id} value={u.id}>
                {u.name} ({u.code})
              </MenuItem>
            ))}
          </TextField>
        </Grid>
      </Grid>

      <Button
        variant="contained"
        startIcon={<PersonAddIcon />}
        onClick={handleAssign}
        disabled={!userId.trim()}
        sx={{ mb: 3 }}
      >
        {t('organization.roles.assign') || 'Assegna ruolo'}
      </Button>

      {userId && (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('organization.roles.role') || 'Ruolo'}</TableCell>
                <TableCell>{t('organization.roles.scope_type') || 'Ambito'}</TableCell>
                <TableCell>{t('organization.roles.scope_unit') || 'Struttura'}</TableCell>
                <TableCell>{t('organization.roles.granted_by') || 'Assegnato da'}</TableCell>
                <TableCell>{t('organization.roles.granted_at') || 'Assegnato il'}</TableCell>
                <TableCell align="right">{t('common.actions') || 'Azioni'}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {assignments.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} align="center">
                    {t('organization.roles.no_assignments') || 'Nessun ruolo assegnato'}
                  </TableCell>
                </TableRow>
              ) : (
                assignments.map((a) => (
                  <TableRow key={a.id}>
                    <TableCell>
                      <Chip size="small" label={roleName(a.role_code)} />
                    </TableCell>
                    <TableCell>{t(`organization.roles.scope.${a.scope_type}`) || a.scope_type}</TableCell>
                    <TableCell>{unitName(a.organizational_unit_id)}</TableCell>
                    <TableCell>{a.granted_by || '—'}</TableCell>
                    <TableCell>{a.granted_at ? new Date(a.granted_at).toLocaleString() : '—'}</TableCell>
                    <TableCell align="right">
                      <Tooltip title={t('organization.roles.revoke') || 'Revoca'}>
                        <IconButton size="small" onClick={() => handleRevoke(a.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  );
};

export default PrivacyRolesPanel;
