import React, { useState } from 'react';
import {
  Box,
  Button,
  Typography,
  Container,
  Tabs,
  Tab,
  Paper,
} from '@mui/material';
import SettingsIcon from '@mui/icons-material/Settings';
import PeopleIcon from '@mui/icons-material/People';
import TuneIcon from '@mui/icons-material/Tune';
import VerifiedUserIcon from '@mui/icons-material/VerifiedUser';
import LockIcon from '@mui/icons-material/Lock';
import BackupIcon from '@mui/icons-material/Backup';
import { PramaLayout } from '../components/PramaLayout';
import { UsersPanel } from '../components/UsersPanel';
import LicensePanel from '../components/LicensePanel';
import { BackupSection } from '../components/BackupSection';
import { DemoDataPanel } from '../components/DemoDataPanel';
import { canAccessSettings } from '../services/authGuard';
import { Link as RouterLink, Navigate } from 'react-router-dom';

interface TabPanelProps {
  children?: React.ReactNode;
  index: number;
  value: number;
}

function TabPanel({ children, value, index }: TabPanelProps) {
  return (
    <div role="tabpanel" hidden={value !== index}>
      {value === index && <Box sx={{ py: 3 }}>{children}</Box>}
    </div>
  );
}

export const SettingsPage: React.FC = () => {
  const [activeTab, setActiveTab] = useState(0);

  // Verifica accesso
  if (!canAccessSettings()) {
    return <Navigate to="/" replace />;
  }

  return (
    <PramaLayout pageTitle="Impostazioni">
      <Container maxWidth="lg">
        {/* Header */}
        <Box sx={{ mb: 3 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <SettingsIcon sx={{ fontSize: 28, color: 'primary.main' }} />
            <Typography variant="h4" sx={{ fontWeight: 600 }}>
              Impostazioni
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            Configura le impostazioni dell'applicazione PrivacyManager
          </Typography>
        </Box>

        {/* Tabs */}
        <Paper sx={{ mb: 3 }}>
          <Tabs
            value={activeTab}
            onChange={(_, v) => setActiveTab(v)}
            variant="scrollable"
            scrollButtons="auto"
            sx={{
              borderBottom: 1,
              borderColor: 'divider',
              '& .MuiTab-root': {
                textTransform: 'none',
                minHeight: 56,
                fontWeight: 500,
              },
            }}
          >
            <Tab
              icon={<PeopleIcon fontSize="small" />}
              iconPosition="start"
              label="Utenti"
            />
            <Tab
              icon={<LockIcon fontSize="small" />}
              iconPosition="start"
              label="Permessi"
            />
            <Tab
              icon={<TuneIcon fontSize="small" />}
              iconPosition="start"
              label="Configurazione"
            />
            <Tab
              icon={<VerifiedUserIcon fontSize="small" />}
              iconPosition="start"
              label="Licenza"
            />
            <Tab
              icon={<BackupIcon fontSize="small" />}
              iconPosition="start"
              label="Backup"
            />
          </Tabs>

          {/* Tab: Utenti */}
          <TabPanel value={activeTab} index={0}>
            <UsersPanel />
          </TabPanel>

          {/* Tab: Permessi */}
          <TabPanel value={activeTab} index={1}>
            <Paper sx={{ p: 3, display: 'flex', flexDirection: 'column', gap: 2 }}>
              <Typography variant="h6">Ruoli e permessi in PrivacyManager</Typography>
              <Typography variant="body2" color="text.secondary">
                Chi può fare cosa dipende da due impostazioni, entrambe per ente.
              </Typography>
              <Box>
                <Typography variant="subtitle2">Ruoli privacy delle persone</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  A chi è assegnato quale ruolo (privacy manager, DPO, referente, operatore, integrazione HelpDesk…) e su
                  quale ambito: tutto l'ente, una struttura o una struttura con le sottostrutture.
                </Typography>
                <Button variant="outlined" component={RouterLink} to="/organizzazione?tab=roles">Apri Ruoli privacy</Button>
              </Box>
              <Box>
                <Typography variant="subtitle2">Permessi di ogni ruolo</Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  Cosa può vedere, creare, modificare e approvare ciascun ruolo in ogni modulo, e la separazione dei compiti.
                </Typography>
                <Button variant="outlined" component={RouterLink} to="/policy">Apri Policy</Button>
              </Box>
            </Paper>
          </TabPanel>

          <TabPanel value={activeTab} index={2}>
            <DemoDataPanel />
          </TabPanel>

          {/* Tab: Licenza */}
          <TabPanel value={activeTab} index={3}>
            <LicensePanel />
          </TabPanel>

          {/* Tab: Backup */}
          <TabPanel value={activeTab} index={4}>
            <BackupSection />
          </TabPanel>
        </Paper>
      </Container>
    </PramaLayout>
  );
};

export default SettingsPage;

