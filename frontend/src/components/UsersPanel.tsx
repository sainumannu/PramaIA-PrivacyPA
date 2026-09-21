import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TablePagination,
  Paper,
  TextField,
  InputAdornment,
  Chip,
  CircularProgress,
  Alert,
  IconButton,
  Tooltip,
  Select,
  MenuItem,
  FormControl,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import RefreshIcon from '@mui/icons-material/Refresh';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import RemoveCircleOutlineIcon from '@mui/icons-material/RemoveCircleOutline';
import { apiClient } from '../services/apiClient';
import { authGuard } from '../services/authGuard';

// Ruoli specifici dell'applicazione (personalizzabili dallo sviluppatore)
const APP_ROLES = [
  { value: 'user', label: 'Utente', color: 'default' as const },
  { value: 'operator', label: 'Operatore', color: 'primary' as const },
  { value: 'admin', label: 'Admin', color: 'error' as const },
];

interface AppUser {
  id: string;
  email: string;
  display_name: string;
  roles: string[];
  is_admin: boolean;
  app_role?: string; // Ruolo specifico in questa app
  is_active?: boolean;
}

interface AppTeamMember {
  id: number;
  user_id: string;
  email: string;
  display_name?: string;
  app_role: string;
  is_active: boolean;
  added_at: string;
}

export const UsersPanel: React.FC = () => {
  const currentUser = authGuard.getCurrentUser();
  
  // State per utenti disponibili (dal Portal)
  const [availableUsers, setAvailableUsers] = useState<AppUser[]>([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
  const [usersError, setUsersError] = useState<string | null>(null);
  
  // State per team members (utenti con ruolo in questa app)
  const [teamMembers, setTeamMembers] = useState<AppTeamMember[]>([]);
  const [loadingTeam, setLoadingTeam] = useState(false);
  
  // Pagination e ricerca
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);
  const [searchTerm, setSearchTerm] = useState('');
  
  // Ruoli selezionati per ogni utente
  const [selectedRoles, setSelectedRoles] = useState<Record<string, string>>({});

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    await Promise.all([loadAvailableUsers(), loadTeamMembers()]);
  };

  const loadAvailableUsers = async () => {
    setLoadingUsers(true);
    setUsersError(null);
    try {
      const users = await apiClient.get<AppUser[]>('/api/settings/users');
      setAvailableUsers(users || []);
    } catch (error: any) {
      console.error('Errore caricamento utenti:', error);
      setUsersError(error.message || 'Impossibile caricare gli utenti');
      setAvailableUsers([]);
    } finally {
      setLoadingUsers(false);
    }
  };

  const loadTeamMembers = async () => {
    setLoadingTeam(true);
    try {
      const members = await apiClient.get<AppTeamMember[]>('/api/settings/team');
      setTeamMembers(members || []);
    } catch (error: any) {
      console.error('Errore caricamento team:', error);
    } finally {
      setLoadingTeam(false);
    }
  };

  const handleAddMember = async (userId: string, role: string) => {
    try {
      await apiClient.post('/api/settings/team', { user_id: userId, app_role: role });
      // Reset Select per questo utente
      setSelectedRoles((prev) => {
        const updated = { ...prev };
        delete updated[userId];
        return updated;
      });
      await loadData();
      setUsersError(null);
    } catch (error: any) {
      console.error('Errore aggiunta membro:', error);
      setUsersError(error.message || 'Impossibile aggiungere utente al team');
    }
  };

  const handleRemoveMember = async (memberId: number) => {
    try {
      await apiClient.delete(`/api/settings/team/${memberId}`);
      await loadTeamMembers();
    } catch (error: any) {
      console.error('Errore rimozione membro:', error);
      setUsersError(error.message || 'Impossibile rimuovere utente');
    }
  };

  // Filtra utenti per ricerca
  const filteredUsers = availableUsers.filter((user) => {
    if (!searchTerm) return true;
    const search = searchTerm.toLowerCase();
    return (
      user.email.toLowerCase().includes(search) ||
      (user.display_name || '').toLowerCase().includes(search)
    );
  });

  // Paginazione
  const paginatedUsers = filteredUsers.slice(
    page * rowsPerPage,
    page * rowsPerPage + rowsPerPage
  );

  // Utenti che sono già nel team
  const teamUserIds = new Set(teamMembers.map((m) => m.user_id));

  return (
    <Box sx={{ p: 3 }}>
      {/* Team Members attivi */}
      <Box sx={{ mb: 4 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            Team Applicazione ({teamMembers.filter((m) => m.is_active).length})
          </Typography>
          <Tooltip title="Ricarica">
            <IconButton size="small" onClick={loadData} disabled={loadingUsers || loadingTeam}>
              <RefreshIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        {loadingTeam ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={24} />
          </Box>
        ) : teamMembers.filter((m) => m.is_active).length === 0 ? (
          <Alert severity="info" sx={{ mb: 2 }}>
            Nessun utente nel team. Aggiungi utenti dalla lista sottostante.
          </Alert>
        ) : (
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell sx={{ fontWeight: 600 }}>Utente</TableCell>
                  <TableCell sx={{ fontWeight: 600 }}>Email</TableCell>
                  <TableCell sx={{ fontWeight: 600 }}>Ruolo App</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 600, width: 80 }}>
                    Azioni
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {teamMembers
                  .filter((m) => m.is_active)
                  .map((member) => (
                    <TableRow key={member.id} hover>
                      <TableCell>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>
                          {member.display_name || member.email}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" color="text.secondary">
                          {member.email}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={APP_ROLES.find((r) => r.value === member.app_role)?.label || member.app_role}
                          size="small"
                          color={APP_ROLES.find((r) => r.value === member.app_role)?.color || 'default'}
                          sx={{ height: 22, fontSize: '0.75rem' }}
                        />
                      </TableCell>
                      <TableCell align="right">
                        <Tooltip title="Rimuovi dal team">
                          <IconButton
                            size="small"
                            color="error"
                            onClick={() => handleRemoveMember(member.id)}
                          >
                            <RemoveCircleOutlineIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Box>

      {/* Errori */}
      {usersError && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setUsersError(null)}>
          {usersError}
        </Alert>
      )}

      {/* Utenti disponibili dal Portal */}
      <Box>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            Utenti Disponibili
          </Typography>
          <TextField
            size="small"
            placeholder="Cerca per nome o email..."
            value={searchTerm}
            onChange={(e) => {
              setSearchTerm(e.target.value);
              setPage(0);
            }}
            sx={{ width: 280 }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
        </Box>

        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Utenti autorizzati a questa app nel Portal. Seleziona un ruolo e clicca + per aggiungerli al team.
        </Typography>

        {loadingUsers ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress />
          </Box>
        ) : filteredUsers.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
            {searchTerm ? `Nessun utente trovato per "${searchTerm}"` : 'Nessun utente disponibile'}
          </Typography>
        ) : (
          <>
            <TableContainer component={Paper} variant="outlined">
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ fontWeight: 600 }}>Utente</TableCell>
                    <TableCell sx={{ fontWeight: 600 }}>Email</TableCell>
                    <TableCell sx={{ fontWeight: 600 }}>Ruoli Portal</TableCell>
                    <TableCell sx={{ fontWeight: 600, width: 150 }}>Ruolo App</TableCell>
                    <TableCell align="right" sx={{ fontWeight: 600, width: 80 }}>
                      Azioni
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {paginatedUsers.map((user) => {
                    const isInTeam = teamUserIds.has(user.id);
                    return (
                      <TableRow
                        key={user.id}
                        hover
                        sx={{ opacity: isInTeam ? 0.5 : 1 }}
                      >
                        <TableCell>
                          <Typography variant="body2" sx={{ fontWeight: 500 }}>
                            {user.display_name || user.email}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" color="text.secondary">
                            {user.email}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                            {user.roles.map((role) => (
                              <Chip
                                key={role}
                                label={role}
                                size="small"
                                variant="outlined"
                                sx={{ height: 20, fontSize: '0.7rem' }}
                              />
                            ))}
                          </Box>
                        </TableCell>
                        <TableCell>
                          {isInTeam ? (
                            <Chip label="Nel team" size="small" color="success" sx={{ height: 22 }} />
                          ) : (
                            <FormControl size="small" fullWidth>
                              <Select
                                value={selectedRoles[user.id] || ''}
                                onChange={(e) =>
                                  setSelectedRoles((prev) => ({
                                    ...prev,
                                    [user.id]: e.target.value,
                                  }))
                                }
                                displayEmpty
                                sx={{ fontSize: '0.85rem' }}
                              >
                                <MenuItem value="" disabled>
                                  <em>Seleziona...</em>
                                </MenuItem>
                                {APP_ROLES.map((role) => (
                                  <MenuItem key={role.value} value={role.value}>
                                    {role.label}
                                  </MenuItem>
                                ))}
                              </Select>
                            </FormControl>
                          )}
                        </TableCell>
                        <TableCell align="right">
                          {!isInTeam && (
                            <Tooltip title="Aggiungi al team">
                              <span>
                                <IconButton
                                  size="small"
                                  color="primary"
                                  onClick={() => handleAddMember(user.id, selectedRoles[user.id])}
                                  disabled={!selectedRoles[user.id]}
                                >
                                  <PersonAddIcon fontSize="small" />
                                </IconButton>
                              </span>
                            </Tooltip>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>

            <TablePagination
              component="div"
              count={filteredUsers.length}
              page={page}
              rowsPerPage={rowsPerPage}
              onPageChange={(_, p) => setPage(p)}
              onRowsPerPageChange={(e) => {
                setRowsPerPage(parseInt(e.target.value, 10));
                setPage(0);
              }}
              rowsPerPageOptions={[5, 10, 25]}
              labelRowsPerPage="Righe per pagina"
            />
          </>
        )}
      </Box>
    </Box>
  );
};

export default UsersPanel;

