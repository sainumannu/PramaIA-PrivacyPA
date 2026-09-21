import React, { useState, useEffect } from 'react';
import {
  Box,
  Tabs,
  Tab,
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
  Checkbox,
  FormControlLabel,
  FormGroup,
  Alert,
  Chip,
  Tooltip,
} from '@mui/material';
import {
  Add as AddIcon,
  Edit as EditIcon,
  Delete as DeleteIcon,
  PersonAdd as PersonAddIcon,
} from '@mui/icons-material';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel(props: TabPanelProps) {
  const { children, value, index, ...other } = props;
  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`permissions-tabpanel-${index}`}
      aria-labelledby={`permissions-tab-${index}`}
      {...other}
    >
      {value === index && <Box sx={{ p: 3 }}>{children}</Box>}
    </div>
  );
}

interface AppRole {
  id: string;
  name: string;
  description: string;
  tenant_id: string | null;
  can_create: boolean;
  can_read: boolean;
  can_update: boolean;
  can_delete: boolean;
  can_assign: boolean;
  can_configure: boolean;
  permissions_json: Record<string, any> | null;
}

interface UserRole {
  role_id: string;
  role_name: string;
  granted_at: string;
  granted_by: string;
}

interface AuditLog {
  id: number;
  timestamp: string;
  action: string;
  actor_user_id: string;
  target_user_id: string | null;
  details: Record<string, any> | null;
  reason: string | null;
}

export const PermissionsPanel: React.FC = () => {
  const { t } = useTranslation('settings');
  const [tabIndex, setTabIndex] = useState(0);

  // Roles state
  const [roles, setRoles] = useState<AppRole[]>([]);
  const [roleDialogOpen, setRoleDialogOpen] = useState(false);
  const [editingRole, setEditingRole] = useState<AppRole | null>(null);
  const [newRole, setNewRole] = useState<Partial<AppRole>>({
    id: '',
    name: '',
    description: '',
    can_create: false,
    can_read: true,
    can_update: false,
    can_delete: false,
    can_assign: false,
    can_configure: false,
  });

  // User roles state
  const [selectedUserId, setSelectedUserId] = useState('');
  const [userRoles, setUserRoles] = useState<UserRole[]>([]);
  const [assignDialogOpen, setAssignDialogOpen] = useState(false);
  const [roleToAssign, setRoleToAssign] = useState('');

  // Audit logs state
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [auditFilterUser, setAuditFilterUser] = useState('');

  // Error/success state
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Load roles
  const loadRoles = async () => {
    try {
      const data = await apiClient.get<AppRole[]>('/api/permissions/roles');
      setRoles(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load roles');
    }
  };

  // Load user roles
  const loadUserRoles = async (userId: string) => {
    if (!userId) return;
    try {
      const data = await apiClient.get<UserRole[]>(`/api/permissions/users/${userId}/roles`);
      setUserRoles(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load user roles');
    }
  };

  // Load audit logs
  const loadAuditLogs = async () => {
    try {
      const params = auditFilterUser ? `?user_id=${auditFilterUser}` : '';
      const data = await apiClient.get<AuditLog[]>(`/api/permissions/audit-logs${params}`);
      setAuditLogs(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load audit logs');
    }
  };

  useEffect(() => {
    loadRoles();
  }, []);

  useEffect(() => {
    if (tabIndex === 2) {
      loadAuditLogs();
    }
  }, [tabIndex, auditFilterUser]);

  // Create/Update role
  const handleSaveRole = async () => {
    try {
      if (editingRole) {
        // Update
        await apiClient.patch(`/api/permissions/roles/${editingRole.id}`, newRole);
        setSuccess('Role updated successfully');
      } else {
        // Create
        await apiClient.post('/api/permissions/roles', newRole);
        setSuccess('Role created successfully');
      }
      setRoleDialogOpen(false);
      setEditingRole(null);
      setNewRole({
        id: '',
        name: '',
        description: '',
        can_create: false,
        can_read: true,
        can_update: false,
        can_delete: false,
        can_assign: false,
        can_configure: false,
      });
      loadRoles();
    } catch (err: any) {
      setError(err.message || 'Failed to save role');
    }
  };

  // Delete role
  const handleDeleteRole = async (roleId: string) => {
    if (!window.confirm(`Delete role "${roleId}"? All users with this role will lose it.`)) {
      return;
    }
    try {
      await apiClient.delete(`/api/permissions/roles/${roleId}`);
      setSuccess('Role deleted successfully');
      loadRoles();
    } catch (err: any) {
      setError(err.message || 'Failed to delete role');
    }
  };

  // Assign role to user
  const handleAssignRole = async () => {
    if (!selectedUserId || !roleToAssign) {
      setError('Please select both user and role');
      return;
    }
    try {
      await apiClient.post('/api/permissions/users/assign-role', {
        user_id: selectedUserId,
        role_id: roleToAssign,
      });
      setSuccess('Role assigned successfully');
      setAssignDialogOpen(false);
      setRoleToAssign('');
      loadUserRoles(selectedUserId);
    } catch (err: any) {
      setError(err.message || 'Failed to assign role');
    }
  };

  // Revoke role from user
  const handleRevokeRole = async (userId: string, roleId: string) => {
    if (!window.confirm(`Revoke role "${roleId}" from user?`)) {
      return;
    }
    try {
      await apiClient.delete(`/api/permissions/users/${userId}/roles/${roleId}`);
      setSuccess('Role revoked successfully');
      loadUserRoles(userId);
    } catch (err: any) {
      setError(err.message || 'Failed to revoke role');
    }
  };

  const handleOpenRoleDialog = (role?: AppRole) => {
    if (role) {
      setEditingRole(role);
      setNewRole({ ...role });
    } else {
      setEditingRole(null);
      setNewRole({
        id: '',
        name: '',
        description: '',
        can_create: false,
        can_read: true,
        can_update: false,
        can_delete: false,
        can_assign: false,
        can_configure: false,
      });
    }
    setRoleDialogOpen(true);
  };

  return (
    <Paper sx={{ width: '100%', mt: 2 }}>
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ m: 2 }}>
          {error}
        </Alert>
      )}
      {success && (
        <Alert severity="success" onClose={() => setSuccess(null)} sx={{ m: 2 }}>
          {success}
        </Alert>
      )}

      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Tabs value={tabIndex} onChange={(_, newValue) => setTabIndex(newValue)}>
          <Tab label={t('permissions.roles-tab') || 'Roles'} />
          <Tab label={t('permissions.user-roles-tab') || 'User Roles'} />
          <Tab label={t('permissions.audit-tab') || 'Audit Logs'} />
        </Tabs>
      </Box>

      {/* Tab 0: Roles */}
      <TabPanel value={tabIndex} index={0}>
        <Box sx={{ mb: 2, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Typography variant="h6">{t('permissions.app-roles') || 'Application Roles'}</Typography>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => handleOpenRoleDialog()}
          >
            {t('permissions.create-role') || 'Create Role'}
          </Button>
        </Box>

        <TableContainer>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell>{t('permissions.role-id') || 'Role ID'}</TableCell>
                <TableCell>{t('permissions.role-name') || 'Name'}</TableCell>
                <TableCell>{t('permissions.description') || 'Description'}</TableCell>
                <TableCell>{t('permissions.permissions') || 'Permissions'}</TableCell>
                <TableCell>{t('common.actions') || 'Actions'}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {roles.map((role) => (
                <TableRow key={role.id}>
                  <TableCell>
                    <code>{role.id}</code>
                    {role.tenant_id && <Chip label="Tenant-specific" size="small" sx={{ ml: 1 }} />}
                  </TableCell>
                  <TableCell>{role.name}</TableCell>
                  <TableCell>{role.description || '—'}</TableCell>
                  <TableCell>
                    {role.can_create && <Chip label="Create" size="small" sx={{ mr: 0.5 }} />}
                    {role.can_read && <Chip label="Read" size="small" sx={{ mr: 0.5 }} />}
                    {role.can_update && <Chip label="Update" size="small" sx={{ mr: 0.5 }} />}
                    {role.can_delete && <Chip label="Delete" size="small" sx={{ mr: 0.5 }} />}
                    {role.can_assign && <Chip label="Assign" size="small" sx={{ mr: 0.5 }} />}
                    {role.can_configure && <Chip label="Configure" size="small" sx={{ mr: 0.5 }} />}
                  </TableCell>
                  <TableCell>
                    <Tooltip title={t('common.edit') || 'Edit'}>
                      <IconButton size="small" onClick={() => handleOpenRoleDialog(role)}>
                        <EditIcon />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title={t('common.delete') || 'Delete'}>
                      <IconButton size="small" onClick={() => handleDeleteRole(role.id)}>
                        <DeleteIcon />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </TabPanel>

      {/* Tab 1: User Roles */}
      <TabPanel value={tabIndex} index={1}>
        <Box sx={{ mb: 2 }}>
          <Typography variant="h6" sx={{ mb: 2 }}>
            {t('permissions.user-roles') || 'User Role Assignments'}
          </Typography>
          <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', mb: 2 }}>
            <TextField
              label={t('permissions.user-id') || 'User ID (email)'}
              value={selectedUserId}
              onChange={(e) => {
                setSelectedUserId(e.target.value);
                if (e.target.value) {
                  loadUserRoles(e.target.value);
                } else {
                  setUserRoles([]);
                }
              }}
              fullWidth
            />
            <Button
              variant="contained"
              startIcon={<PersonAddIcon />}
              onClick={() => setAssignDialogOpen(true)}
              disabled={!selectedUserId}
            >
              {t('permissions.assign-role') || 'Assign Role'}
            </Button>
          </Box>
        </Box>

        {selectedUserId && (
          <TableContainer>
            <Table>
              <TableHead>
                <TableRow>
                  <TableCell>{t('permissions.role-id') || 'Role ID'}</TableCell>
                  <TableCell>{t('permissions.role-name') || 'Role Name'}</TableCell>
                  <TableCell>{t('permissions.granted-at') || 'Granted At'}</TableCell>
                  <TableCell>{t('permissions.granted-by') || 'Granted By'}</TableCell>
                  <TableCell>{t('common.actions') || 'Actions'}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {userRoles.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} align="center">
                      {t('permissions.no-roles') || 'No roles assigned'}
                    </TableCell>
                  </TableRow>
                ) : (
                  userRoles.map((ur) => (
                    <TableRow key={ur.role_id}>
                      <TableCell><code>{ur.role_id}</code></TableCell>
                      <TableCell>{ur.role_name}</TableCell>
                      <TableCell>{new Date(ur.granted_at).toLocaleString()}</TableCell>
                      <TableCell>{ur.granted_by}</TableCell>
                      <TableCell>
                        <Tooltip title={t('permissions.revoke-role') || 'Revoke'}>
                          <IconButton
                            size="small"
                            onClick={() => handleRevokeRole(selectedUserId, ur.role_id)}
                          >
                            <DeleteIcon />
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
      </TabPanel>

      {/* Tab 2: Audit Logs */}
      <TabPanel value={tabIndex} index={2}>
        <Box sx={{ mb: 2 }}>
          <Typography variant="h6" sx={{ mb: 2 }}>
            {t('permissions.audit-logs') || 'Permission Audit Logs'}
          </Typography>
          <TextField
            label={t('permissions.filter-by-user') || 'Filter by User ID'}
            value={auditFilterUser}
            onChange={(e) => setAuditFilterUser(e.target.value)}
            fullWidth
            sx={{ mb: 2 }}
          />
        </Box>

        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t('permissions.timestamp') || 'Timestamp'}</TableCell>
                <TableCell>{t('permissions.action') || 'Action'}</TableCell>
                <TableCell>{t('permissions.actor') || 'Actor'}</TableCell>
                <TableCell>{t('permissions.target') || 'Target'}</TableCell>
                <TableCell>{t('permissions.details') || 'Details'}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {auditLogs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} align="center">
                    {t('permissions.no-logs') || 'No audit logs found'}
                  </TableCell>
                </TableRow>
              ) : (
                auditLogs.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell>{new Date(log.timestamp).toLocaleString()}</TableCell>
                    <TableCell>
                      <Chip label={log.action} size="small" />
                    </TableCell>
                    <TableCell>{log.actor_user_id}</TableCell>
                    <TableCell>{log.target_user_id || '—'}</TableCell>
                    <TableCell>
                      <code>{JSON.stringify(log.details)}</code>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </TabPanel>

      {/* Role Create/Edit Dialog */}
      <Dialog open={roleDialogOpen} onClose={() => setRoleDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>
          {editingRole
            ? t('permissions.edit-role') || 'Edit Role'
            : t('permissions.create-role') || 'Create Role'}
        </DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            <TextField
              label={t('permissions.role-id') || 'Role ID'}
              value={newRole.id || ''}
              onChange={(e) => setNewRole({ ...newRole, id: e.target.value })}
              disabled={!!editingRole}
              fullWidth
            />
            <TextField
              label={t('permissions.role-name') || 'Role Name'}
              value={newRole.name || ''}
              onChange={(e) => setNewRole({ ...newRole, name: e.target.value })}
              fullWidth
            />
            <TextField
              label={t('permissions.description') || 'Description'}
              value={newRole.description || ''}
              onChange={(e) => setNewRole({ ...newRole, description: e.target.value })}
              multiline
              rows={2}
              fullWidth
            />
            <Typography variant="subtitle2">{t('permissions.permissions') || 'Permissions'}</Typography>
            <FormGroup>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={newRole.can_create || false}
                    onChange={(e) => setNewRole({ ...newRole, can_create: e.target.checked })}
                  />
                }
                label={t('permissions.can-create') || 'Can Create'}
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={newRole.can_read || false}
                    onChange={(e) => setNewRole({ ...newRole, can_read: e.target.checked })}
                  />
                }
                label={t('permissions.can-read') || 'Can Read'}
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={newRole.can_update || false}
                    onChange={(e) => setNewRole({ ...newRole, can_update: e.target.checked })}
                  />
                }
                label={t('permissions.can-update') || 'Can Update'}
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={newRole.can_delete || false}
                    onChange={(e) => setNewRole({ ...newRole, can_delete: e.target.checked })}
                  />
                }
                label={t('permissions.can-delete') || 'Can Delete'}
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={newRole.can_assign || false}
                    onChange={(e) => setNewRole({ ...newRole, can_assign: e.target.checked })}
                  />
                }
                label={t('permissions.can-assign') || 'Can Assign'}
              />
              <FormControlLabel
                control={
                  <Checkbox
                    checked={newRole.can_configure || false}
                    onChange={(e) => setNewRole({ ...newRole, can_configure: e.target.checked })}
                  />
                }
                label={t('permissions.can-configure') || 'Can Configure'}
              />
            </FormGroup>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRoleDialogOpen(false)}>{t('common.cancel') || 'Cancel'}</Button>
          <Button onClick={handleSaveRole} variant="contained">
            {t('common.save') || 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Assign Role Dialog */}
      <Dialog open={assignDialogOpen} onClose={() => setAssignDialogOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>{t('permissions.assign-role') || 'Assign Role'}</DialogTitle>
        <DialogContent>
          <Box sx={{ pt: 1 }}>
            <TextField
              select
              label={t('permissions.select-role') || 'Select Role'}
              value={roleToAssign}
              onChange={(e) => setRoleToAssign(e.target.value)}
              fullWidth
              SelectProps={{
                native: true,
              }}
            >
              <option value="">-- Select --</option>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name} ({role.id})
                </option>
              ))}
            </TextField>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAssignDialogOpen(false)}>{t('common.cancel') || 'Cancel'}</Button>
          <Button onClick={handleAssignRole} variant="contained">
            {t('common.assign') || 'Assign'}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
};

