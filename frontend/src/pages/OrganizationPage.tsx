import React, { useState } from 'react';
import { Box, Typography, Container, Tabs, Tab, Paper } from '@mui/material';
import AccountBalanceIcon from '@mui/icons-material/AccountBalance';
import ApartmentIcon from '@mui/icons-material/Apartment';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import BadgeIcon from '@mui/icons-material/Badge';
import { PramaLayout } from '../components/PramaLayout';
import { OrganizationEntityPanel } from '../components/OrganizationEntityPanel';
import { OrganizationUnitsPanel } from '../components/OrganizationUnitsPanel';
import { PrivacyRolesPanel } from '../components/PrivacyRolesPanel';
import { useTranslation } from '../i18n/useTranslation';

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

export const OrganizationPage: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [activeTab, setActiveTab] = useState(0);

  return (
    <PramaLayout pageTitle={t('organization.title') || 'Organizzazione'}>
      <Container maxWidth="lg">
        <Box sx={{ mb: 3 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
            <AccountTreeIcon sx={{ fontSize: 28, color: 'primary.main' }} />
            <Typography variant="h4" sx={{ fontWeight: 600 }}>
              {t('organization.title') || 'Organizzazione'}
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {t('organization.subtitle') || "Anagrafica dell'ente, struttura organizzativa e ruoli privacy"}
          </Typography>
        </Box>

        <Paper sx={{ mb: 3 }}>
          <Tabs
            value={activeTab}
            onChange={(_, v) => setActiveTab(v)}
            variant="scrollable"
            scrollButtons="auto"
            sx={{
              borderBottom: 1,
              borderColor: 'divider',
              '& .MuiTab-root': { textTransform: 'none', minHeight: 56, fontWeight: 500 },
            }}
          >
            <Tab icon={<AccountBalanceIcon fontSize="small" />} iconPosition="start" label={t('organization.tabs.entity') || 'Ente'} />
            <Tab icon={<ApartmentIcon fontSize="small" />} iconPosition="start" label={t('organization.tabs.units') || 'Strutture organizzative'} />
            <Tab icon={<BadgeIcon fontSize="small" />} iconPosition="start" label={t('organization.tabs.roles') || 'Ruoli privacy'} />
          </Tabs>

          <TabPanel value={activeTab} index={0}>
            <OrganizationEntityPanel />
          </TabPanel>
          <TabPanel value={activeTab} index={1}>
            <OrganizationUnitsPanel />
          </TabPanel>
          <TabPanel value={activeTab} index={2}>
            <PrivacyRolesPanel />
          </TabPanel>
        </Paper>
      </Container>
    </PramaLayout>
  );
};

export default OrganizationPage;
