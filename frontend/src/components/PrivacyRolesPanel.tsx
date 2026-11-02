import React, { useEffect, useMemo, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  IconButton,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import EditIcon from '@mui/icons-material/Edit';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import { apiClient } from '../services/apiClient';
import { usePermissions } from '../services/permissions';
import { useTranslation } from '../i18n/useTranslation';
import { ErrorBanner } from './ErrorBanner';
import { PersonField, PersonName, usePortalPeople } from './PortalPeople';
import { SinglePick, UnitPicker } from './UnitPicker';

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
  row_version: number;
}

// RECORD non si assegna da qui: un ruolo su un singolo record non ha ancora un record da indicare né effetto sui permessi.
const SCOPE_TYPES = ['TENANT', 'UNIT', 'SUBTREE'];
const needsUnit = (scope: string) => scope === 'UNIT' || scope === 'SUBTREE';

// La scadenza si sceglie come data e vale fino alla fine di quel giorno; il server la conserva in UTC senza fuso.
const toValidTo = (day: string) => (day ? `${day}T23:59:59` : null);
const dayOf = (validTo: string | null) => (validTo ? validTo.slice(0, 10) : '');
const isExpired = (a: UserRoleAssignment) => Boolean(a.valid_to && a.valid_to < new Date().toISOString().slice(0, 19));

// Ambito, struttura e scadenza di un'assegnazione (in assegnazione e in modifica).
const ScopeFields: React.FC<{
  scope: string; unitId: string; validTo: string; onScope: (v: string) => void; onUnit: (v: string) => void;
  onValidTo: (v: string) => void; onUnitAdded: () => void; wide?: boolean;
}> = ({ scope, unitId, validTo, onScope, onUnit, onValidTo, onUnitAdded, wide }) => {
  const { t } = useTranslation('privacypa');
  const size = wide ? 12 : 4;
  return (
    <>
      <Grid item xs={12} sm={size}>
        <TextField
          select
          label={t('organization.roles.scope_type')}
          value={scope}
          onChange={(e) => onScope(e.target.value)}
          helperText={t(`organization.roles.scope_hint.${scope}`)}
          fullWidth
        >
          {SCOPE_TYPES.map((s) => (
            <MenuItem key={s} value={s}>{t(`organization.roles.scope.${s}`)}</MenuItem>
          ))}
        </TextField>
      </Grid>
      <Grid item xs={12} sm={size}>
        <TextField
          type="date"
          label={t('organization.roles.valid_to')}
          value={validTo}
          onChange={(e) => onValidTo(e.target.value)}
          helperText={t('organization.roles.valid_to_hint')}
          InputLabelProps={{ shrink: true }}
          fullWidth
        />
      </Grid>
      {needsUnit(scope) && (
        <Grid item xs={12} sm={wide ? 12 : 8}>
          <UnitPicker label={t('organization.roles.scope_unit')} value={unitId} required onChange={onUnit} onUnitAdded={onUnitAdded} />
        </Grid>
      )}
    </>
  );
};

export const PrivacyRolesPanel: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const { can, loaded } = usePermissions();
  const { nameOf } = usePortalPeople();
  const canView = can('roles.view');
  const canAdminister = can('roles.administer');

  const [catalog, setCatalog] = useState<PrivacyRole[]>([]);
  const [units, setUnits] = useState<OrganizationalUnit[]>([]);
  const [assignments, setAssignments] = useState<UserRoleAssignment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // nuova assegnazione
  const [userId, setUserId] = useState('');
  const [roleCode, setRoleCode] = useState('');
  const [scopeType, setScopeType] = useState('TENANT');
  const [unitId, setUnitId] = useState('');
  const [validTo, setValidTo] = useState('');

  // filtri dell'elenco
  const [search, setSearch] = useState('');
  const [roleFilter, setRoleFilter] = useState('');
  const [unitFilter, setUnitFilter] = useState('');
  const [stateFilter, setStateFilter] = useState('ALL');

  const [editing, setEditing] = useState<UserRoleAssignment | null>(null);
  const [editScope, setEditScope] = useState('TENANT');
  const [editUnit, setEditUnit] = useState('');
  const [editValidTo, setEditValidTo] = useState('');

  const loadCatalog = async () => {
    try {
      setCatalog(await apiClient.get<PrivacyRole[]>('/api/privacypa/roles/catalog'));
    } catch (err: any) {
      setError(err.message);
    }
  };

  const loadUnits = async () => {
    try {
      setUnits(await apiClient.get<OrganizationalUnit[]>('/api/privacypa/organizational-units'));
    } catch {
      // silenzioso: le strutture servono solo per i nomi e per gli ambiti su una struttura
    }
  };

  const loadAssignments = async () => {
    if (!loaded || !canView) return;          // prima dei permessi `can` risponde sì: niente 403 inutili
    try {
      setAssignments(await apiClient.get<UserRoleAssignment[]>('/api/privacypa/roles/assignments'));
    } catch (err: any) {
      setError(err.message);
    }
  };

  useEffect(() => {
    loadCatalog();
    loadUnits();
  }, []);

  useEffect(() => {
    loadAssignments();
  }, [loaded, canView]);

  const roleName = (code: string) => catalog.find((r) => r.code === code)?.name || code;
  const unitName = (id: string | null) => (id ? units.find((u) => u.id === id)?.name || id : '—');

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return assignments.filter((a) =>
      (!q || a.user_id.toLowerCase().includes(q) || nameOf(a.user_id).toLowerCase().includes(q))
      && (!roleFilter || a.role_code === roleFilter)
      && (!unitFilter || a.organizational_unit_id === unitFilter)
      && (stateFilter === 'ALL' || (stateFilter === 'EXPIRED') === isExpired(a)));
  }, [assignments, search, roleFilter, unitFilter, stateFilter, nameOf]);

  const handleAssign = async () => {
    if (!userId.trim() || !roleCode) {
      setError(t('organization.roles.select_user_and_role'));
      return;
    }
    if (needsUnit(scopeType) && !unitId) {
      setError(t('organization.roles.unit_required'));
      return;
    }
    try {
      await apiClient.post('/api/privacypa/roles/assign', {
        user_id: userId,
        role_code: roleCode,
        scope_type: scopeType,
        organizational_unit_id: needsUnit(scopeType) ? unitId : null,
        valid_to: toValidTo(validTo),
      });
      setSuccess(t('organization.roles.assigned'));
      setRoleCode('');
      setScopeType('TENANT');
      setUnitId('');
      setValidTo('');
      loadAssignments();
    } catch (err: any) {
      setError(err.message || t('organization.roles.assign_failed'));
    }
  };

  const openEdit = (a: UserRoleAssignment) => {
    setEditing(a);
    setEditScope(a.scope_type);
    setEditUnit(a.organizational_unit_id || '');
    setEditValidTo(dayOf(a.valid_to));
  };

  const handleUpdate = async () => {
    if (!editing) return;
    if (needsUnit(editScope) && !editUnit) {
      setError(t('organization.roles.unit_required'));
      return;
    }
    try {
      await apiClient.put(`/api/privacypa/roles/${editing.id}`, {
        scope_type: editScope,
        organizational_unit_id: needsUnit(editScope) ? editUnit : null,
        valid_to: toValidTo(editValidTo),
      }, { version: editing.row_version });
      setSuccess(t('organization.roles.updated'));
      setEditing(null);
      loadAssignments();
    } catch (err: any) {
      setError(err.message || t('organization.roles.update_failed'));
    }
  };

  const handleRevoke = async (a: UserRoleAssignment) => {
    if (!window.confirm(t('organization.roles.confirm_revoke_of', { role: roleName(a.role_code), name: nameOf(a.user_id) }))) {
      return;
    }
    try {
      await apiClient.delete(`/api/privacypa/roles/${a.id}`);
      setSuccess(t('organization.roles.revoked'));
      loadAssignments();
    } catch (err: any) {
      setError(err.message || t('organization.roles.revoke_failed'));
    }
  };

  return (
    <Paper sx={{ p: 3 }}>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {success && (
        <Alert severity="success" onClose={() => setSuccess(null)} sx={{ mb: 2 }}>
          {success}
        </Alert>
      )}

      {canAdminister && (
        <>
          <Typography variant="h6" sx={{ mb: 2 }}>{t('organization.roles.assign_title')}</Typography>
          <Grid container spacing={2} sx={{ mb: 2 }}>
            <Grid item xs={12} sm={8}>
              <PersonField label={t('organization.roles.user_id')} value={userId} hint={t('organization.roles.user_hint')} onChange={setUserId} />
            </Grid>
            <Grid item xs={12} sm={4}>
              <TextField select label={t('organization.roles.role')} value={roleCode} onChange={(e) => setRoleCode(e.target.value)} fullWidth>
                <MenuItem value="">—</MenuItem>
                {catalog.map((role) => (
                  <MenuItem key={role.code} value={role.code}>{role.name}</MenuItem>
                ))}
              </TextField>
            </Grid>
            <ScopeFields
              scope={scopeType} unitId={unitId} validTo={validTo}
              onScope={setScopeType} onUnit={setUnitId} onValidTo={setValidTo} onUnitAdded={loadUnits}
            />
          </Grid>
          <Button variant="contained" startIcon={<PersonAddIcon />} onClick={handleAssign} disabled={!userId.trim()} sx={{ mb: 4 }}>
            {t('organization.roles.assign')}
          </Button>
        </>
      )}

      <Typography variant="h6" sx={{ mb: 1 }}>{t('organization.roles.assignments_title')}</Typography>
      {!canView ? (
        <Alert severity="info" sx={{ mb: 3 }}>{t('organization.roles.no_view_permission')}</Alert>
      ) : (
        <>
          <Grid container spacing={2} sx={{ mb: 2 }}>
            <Grid item xs={12} sm={3}>
              <TextField label={t('organization.roles.filter_person')} value={search} onChange={(e) => setSearch(e.target.value)} fullWidth />
            </Grid>
            <Grid item xs={12} sm={3}>
              <TextField select label={t('organization.roles.role')} value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} fullWidth>
                <MenuItem value="">{t('organization.roles.filter_all')}</MenuItem>
                {catalog.map((role) => (
                  <MenuItem key={role.code} value={role.code}>{role.name}</MenuItem>
                ))}
              </TextField>
            </Grid>
            <Grid item xs={12} sm={3}>
              <SinglePick
                label={t('organization.roles.filter_unit')}
                items={units.map((u) => ({ id: u.id, label: u.name }))}
                value={unitFilter}
                onChange={setUnitFilter}
              />
            </Grid>
            <Grid item xs={12} sm={3}>
              <TextField select label={t('organization.roles.filter_state')} value={stateFilter} onChange={(e) => setStateFilter(e.target.value)} fullWidth>
                {['ALL', 'VALID', 'EXPIRED'].map((s) => (
                  <MenuItem key={s} value={s}>{t(`organization.roles.state.${s}`)}</MenuItem>
                ))}
              </TextField>
            </Grid>
          </Grid>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {t('organization.roles.count', { shown: visible.length, total: assignments.length })}
          </Typography>
          <TableContainer sx={{ mb: 4 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t('organization.roles.user_id')}</TableCell>
                  <TableCell>{t('organization.roles.role')}</TableCell>
                  <TableCell>{t('organization.roles.scope_type')}</TableCell>
                  <TableCell>{t('organization.roles.scope_unit')}</TableCell>
                  <TableCell>{t('organization.roles.valid_to')}</TableCell>
                  <TableCell>{t('organization.roles.granted_by')}</TableCell>
                  <TableCell>{t('organization.roles.granted_at')}</TableCell>
                  {canAdminister && <TableCell align="right">{t('common.actions')}</TableCell>}
                </TableRow>
              </TableHead>
              <TableBody>
                {visible.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={canAdminister ? 8 : 7} align="center">{t('organization.roles.no_assignments')}</TableCell>
                  </TableRow>
                ) : (
                  visible.map((a) => (
                    <TableRow key={a.id} sx={isExpired(a) ? { opacity: 0.6 } : undefined}>
                      <TableCell><PersonName id={a.user_id} /></TableCell>
                      <TableCell><Chip size="small" label={roleName(a.role_code)} /></TableCell>
                      <TableCell>{t(`organization.roles.scope.${a.scope_type}`)}</TableCell>
                      <TableCell>{unitName(a.organizational_unit_id)}</TableCell>
                      <TableCell>
                        {a.valid_to ? new Date(dayOf(a.valid_to)).toLocaleDateString() : t('organization.roles.no_expiry')}
                        {isExpired(a) && <Chip size="small" color="error" label={t('organization.roles.expired')} sx={{ ml: 1 }} />}
                      </TableCell>
                      <TableCell><PersonName id={a.granted_by} /></TableCell>
                      <TableCell>{a.granted_at ? new Date(a.granted_at).toLocaleDateString() : '—'}</TableCell>
                      {canAdminister && (
                        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                          <Tooltip title={t('common.edit')}>
                            <IconButton size="small" onClick={() => openEdit(a)}><EditIcon fontSize="small" /></IconButton>
                          </Tooltip>
                          <Tooltip title={t('organization.roles.revoke')}>
                            <IconButton size="small" onClick={() => handleRevoke(a)}><DeleteIcon fontSize="small" /></IconButton>
                          </Tooltip>
                        </TableCell>
                      )}
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </>
      )}

      <Accordion disableGutters variant="outlined">
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="subtitle1">{t('organization.roles.catalog_title')}</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t('organization.roles.code')}</TableCell>
                  <TableCell>{t('organization.roles.name')}</TableCell>
                  <TableCell>{t('organization.roles.description')}</TableCell>
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
        </AccordionDetails>
      </Accordion>

      <Dialog open={editing !== null} onClose={() => setEditing(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('organization.roles.edit_title')}</DialogTitle>
        <DialogContent>
          {editing && (
            <Box sx={{ pt: 1 }}>
              <Typography variant="body2" sx={{ mb: 2 }}>
                <PersonName id={editing.user_id} /> · <strong>{roleName(editing.role_code)}</strong>
              </Typography>
              <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 2 }}>
                {t('organization.roles.edit_hint')}
              </Typography>
              <Grid container spacing={2}>
                <ScopeFields
                  wide scope={editScope} unitId={editUnit} validTo={editValidTo}
                  onScope={setEditScope} onUnit={setEditUnit} onValidTo={setEditValidTo} onUnitAdded={loadUnits}
                />
              </Grid>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditing(null)}>{t('common.cancel')}</Button>
          <Button variant="contained" onClick={handleUpdate}>{t('common.save')}</Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
};

export default PrivacyRolesPanel;
