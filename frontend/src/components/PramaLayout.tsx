import React, { useMemo, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  AppBar,
  Toolbar,
  Typography,
  Box,
  Chip,
  IconButton,
  Container,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Drawer,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  ListSubheader,
  Divider,
  Snackbar,
  Alert,
  useMediaQuery,
} from '@mui/material';
import { alpha, useTheme } from '@mui/material/styles';
import LogoutIcon from '@mui/icons-material/Logout';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import AdminPanelSettingsIcon from '@mui/icons-material/AdminPanelSettings';
import LinkIcon from '@mui/icons-material/Link';
import FindInPageIcon from '@mui/icons-material/FindInPage';
import ArticleIcon from '@mui/icons-material/Article';
import GavelIcon from '@mui/icons-material/Gavel';
import BuildIcon from '@mui/icons-material/Build';
import ReportIcon from '@mui/icons-material/ReportProblem';
import PersonSearchIcon from '@mui/icons-material/PersonSearch';
import FactCheckOutlinedIcon from '@mui/icons-material/FactCheckOutlined';
import SettingsIcon from '@mui/icons-material/Settings';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import AssignmentIcon from '@mui/icons-material/Assignment';
import DnsIcon from '@mui/icons-material/Dns';
import BusinessIcon from '@mui/icons-material/Business';
import DescriptionIcon from '@mui/icons-material/Description';
import TaskAltIcon from '@mui/icons-material/TaskAlt';
import SearchIcon from '@mui/icons-material/Search';
import ManageSearchIcon from '@mui/icons-material/ManageSearch';
import WebhookIcon from '@mui/icons-material/Webhook';
import HistoryIcon from '@mui/icons-material/History';
import MenuBookIcon from '@mui/icons-material/MenuBook';
import PolicyIcon from '@mui/icons-material/Policy';
import PublicIcon from '@mui/icons-material/Public';
import ReportProblemIcon from '@mui/icons-material/ReportProblem';
import MenuIcon from '@mui/icons-material/Menu';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import DashboardIcon from '@mui/icons-material/Dashboard';
import RocketLaunchIcon from '@mui/icons-material/RocketLaunch';
import LocalOfferIcon from '@mui/icons-material/LocalOffer';
import TuneIcon from '@mui/icons-material/Tune';
import SchoolIcon from '@mui/icons-material/School';
import BadgeIcon from '@mui/icons-material/Badge';
import VerifiedUserIcon from '@mui/icons-material/VerifiedUser';
import SupervisorAccountIcon from '@mui/icons-material/SupervisorAccount';
import RateReviewIcon from '@mui/icons-material/RateReview';
import AssessmentIcon from '@mui/icons-material/Assessment';
import { authGuard, canAccessSettings } from '../services/authGuard';
import { useThemeContext } from '../shared/ThemeProvider';
import { ThemeName, themeOptions } from '../shared/theme';
import LanguageSwitcher from './LanguageSwitcher';
import { NotificationBell } from './NotificationBell';
import { useTranslation } from '../i18n/useTranslation';
import { usePermissions } from '../services/permissions';
import { useTenant } from '../services/tenantContext';

const DRAWER_WIDTH = 272;

interface PramaLayoutProps {
  children: React.ReactNode;
  pageTitle?: string;
}

interface NavItem {
  to: string;
  labelKey: string;
  icon: React.ReactNode;
  permission?: string;
}

export const PramaLayout: React.FC<PramaLayoutProps> = ({ children, pageTitle }) => {
  const user = authGuard.getCurrentUser();
  const PORTAL_URL = process.env.REACT_APP_PORTAL_URL || 'http://localhost:3080';
  const { themeName, setThemeName } = useThemeContext();
  const { t } = useTranslation('common');
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const { can } = usePermissions();
  const { selectedTenant, switchTenant, availableTenants, isLoadingTenants } = useTenant();
  const [isSwitching, setIsSwitching] = useState(false);
  const [switchFailed, setSwitchFailed] = useState(false);

  // Un token = un tenant: il cambio chiede al Portal un nuovo token e ricarica la pagina.
  const handleTenantChange = async (tenantId: string) => {
    setIsSwitching(true);
    try {
      await switchTenant(tenantId);
    } catch {
      setSwitchFailed(true);
      setIsSwitching(false);
    }
  };

  const sections = useMemo(() => {
    const work: NavItem[] = [
      { to: '/', labelKey: 'navigation.home', icon: <DashboardIcon /> },
      { to: '/registro', labelKey: 'navigation.register', icon: <AssignmentIcon />, permission: 'processing.view' },
      { to: '/approvazioni', labelKey: 'navigation.approvals', icon: <FactCheckIcon />, permission: 'approval.view' },
      { to: '/progetti', labelKey: 'navigation.projects', icon: <RocketLaunchIcon />, permission: 'project.view' },
      { to: '/sistemi', labelKey: 'navigation.systems', icon: <DnsIcon />, permission: 'asset.view' },
      { to: '/fornitori', labelKey: 'navigation.suppliers', icon: <BusinessIcon />, permission: 'party.view' },
      { to: '/filiera', labelKey: 'navigation.supply', icon: <LinkIcon />, permission: 'party.view' },
      { to: '/documenti', labelKey: 'navigation.documents', icon: <DescriptionIcon />, permission: 'document.view' },
      { to: '/informative', labelKey: 'navigation.notices', icon: <ArticleIcon />, permission: 'notice.view' },
      { to: '/richieste', labelKey: 'navigation.requests', icon: <PersonSearchIcon />, permission: 'dsr.view' },
      { to: '/violazioni', labelKey: 'navigation.breaches', icon: <ReportIcon />, permission: 'breach.view' },
      { to: '/azioni', labelKey: 'navigation.actions', icon: <BuildIcon />, permission: 'action.view' },
      { to: '/audit-programmati', labelKey: 'navigation.audits', icon: <FactCheckOutlinedIcon />, permission: 'auditplan.view' },
      { to: '/dpo', labelKey: 'navigation.dpo', icon: <SupervisorAccountIcon />, permission: 'dpo.view' },
      { to: '/pareri-dpo', labelKey: 'navigation.opinions', icon: <RateReviewIcon />, permission: 'opinion.view' },
      { to: '/relazione-annuale', labelKey: 'navigation.annualReport', icon: <AssessmentIcon />, permission: 'report.view' },
      { to: '/formazione', labelKey: 'navigation.training', icon: <SchoolIcon />, permission: 'training.view' },
      { to: '/formazione/assegnazioni', labelKey: 'navigation.trainingAssignments', icon: <BadgeIcon />, permission: 'training.view' },
      { to: '/autorizzazioni', labelKey: 'navigation.authorizations', icon: <VerifiedUserIcon />, permission: 'authorization.view' },
      { to: '/task', labelKey: 'navigation.tasks', icon: <TaskAltIcon />, permission: 'task.view' },
      { to: '/dpia', labelKey: 'navigation.dpia', icon: <PolicyIcon />, permission: 'dpia.view' },
      { to: '/trasferimenti', labelKey: 'navigation.transfers', icon: <PublicIcon />, permission: 'transfer.view' },
      { to: '/rischi', labelKey: 'navigation.risks', icon: <ReportProblemIcon />, permission: 'risk.view' },
    ];
    const entity: NavItem[] = [
      { to: '/organizzazione', labelKey: 'navigation.organization', icon: <AccountTreeIcon /> },
      { to: '/cataloghi', labelKey: 'navigation.catalogs', icon: <MenuBookIcon /> },
      { to: '/tag', labelKey: 'navigation.tags', icon: <LocalOfferIcon />, permission: 'tag.view' },
      { to: '/campi-custom', labelKey: 'navigation.customFields', icon: <TuneIcon />, permission: 'customfield.view' },
      { to: '/riferimenti', labelKey: 'navigation.legalref', icon: <GavelIcon />, permission: 'legalref.view' },
      { to: '/profili-autorizzazione', labelKey: 'navigation.authProfiles', icon: <VerifiedUserIcon />, permission: 'authorization.view' },
      { to: '/import', labelKey: 'navigation.import', icon: <UploadFileIcon />, permission: 'import.create' },
      { to: '/policy', labelKey: 'navigation.policy', icon: <AdminPanelSettingsIcon />, permission: 'policy.administer' },
      { to: '/webhook', labelKey: 'navigation.webhooks', icon: <WebhookIcon />, permission: 'webhook.view' },
    ];
    const tools: NavItem[] = [
      { to: '/ricerca', labelKey: 'navigation.search', icon: <SearchIcon /> },
      { to: '/interrogazioni', labelKey: 'navigation.query', icon: <ManageSearchIcon />, permission: 'query.view' },
      { to: '/verifiche', labelKey: 'navigation.findings', icon: <FindInPageIcon />, permission: 'finding.view' },
      { to: '/audit', labelKey: 'navigation.audit', icon: <HistoryIcon />, permission: 'audit.view' },
    ];
    if (canAccessSettings()) {
      tools.push({ to: '/settings', labelKey: 'navigation.settings', icon: <SettingsIcon /> });
    }
    const visible = (items: NavItem[]) => items.filter((item) => !item.permission || can(item.permission));
    return [
      { titleKey: 'navigation.section_work', items: visible(work) },
      { titleKey: 'navigation.section_entity', items: visible(entity) },
      { titleKey: 'navigation.section_tools', items: visible(tools) },
    ].filter((section) => section.items.length > 0);
  }, [user, can]);

  const closeMobile = () => setMobileOpen(false);

  const drawer = (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Box sx={{ px: 2.5, py: 2.25 }}>
        <Typography
          variant="h6"
          sx={{
            background: 'linear-gradient(90deg, #10b981, #06b6d4)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            fontWeight: 800,
            letterSpacing: '-0.5px',
          }}
        >
          PrivacyManager
        </Typography>
        <Typography variant="caption" color="text.secondary">
          PrivacyPA
        </Typography>
      </Box>
      <Divider />
      <Box sx={{ flex: 1, overflowY: 'auto', py: 1 }}>
        {sections.map((section) => (
          <List
            key={section.titleKey}
            dense
            subheader={
              <ListSubheader
                disableSticky
                sx={{ bgcolor: 'transparent', lineHeight: '32px', fontSize: '0.7rem', letterSpacing: '0.06em' }}
              >
                {t(section.titleKey)}
              </ListSubheader>
            }
          >
            {section.items.map((item) => (
              <ListItemButton
                key={item.to}
                component={NavLink}
                to={item.to}
                end={item.to === '/'}
                onClick={closeMobile}
                selected={
                  item.to === '/'
                    ? location.pathname === '/'
                    : location.pathname === item.to || location.pathname.startsWith(`${item.to}/`)
                }
                sx={{
                  mx: 1,
                  mb: 0.25,
                  borderRadius: 1.5,
                  '&.Mui-selected': {
                    backgroundColor: 'rgba(16, 185, 129, 0.16)',
                    color: 'primary.main',
                    '& .MuiListItemIcon-root': { color: 'primary.main' },
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 36, color: 'text.secondary' }}>{item.icon}</ListItemIcon>
                <ListItemText primary={t(item.labelKey)} primaryTypographyProps={{ fontSize: '0.9rem', fontWeight: 500 }} />
              </ListItemButton>
            ))}
          </List>
        ))}
      </Box>
      <Divider />
      <List dense sx={{ py: 1 }}>
        <ListItemButton component="a" href={PORTAL_URL} sx={{ mx: 1, borderRadius: 1.5 }}>
          <ListItemIcon sx={{ minWidth: 36, color: 'text.secondary' }}>
            <OpenInNewIcon />
          </ListItemIcon>
          <ListItemText primary={t('navigation.portal')} primaryTypographyProps={{ fontSize: '0.9rem' }} />
        </ListItemButton>
        <ListItemButton onClick={() => authGuard.logout()} sx={{ mx: 1, borderRadius: 1.5 }}>
          <ListItemIcon sx={{ minWidth: 36, color: 'text.secondary' }}>
            <LogoutIcon />
          </ListItemIcon>
          <ListItemText primary={t('navigation.logout')} primaryTypographyProps={{ fontSize: '0.9rem' }} />
        </ListItemButton>
      </List>
    </Box>
  );

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <AppBar
        position="fixed"
        elevation={0}
        // Colori dal tema: con un fondo fisso scuro il testo dei temi chiari (scuro) non si leggeva
        sx={{
          backgroundColor: (th) => alpha(th.palette.background.paper, 0.92),
          color: 'text.primary',
          backdropFilter: 'blur(12px)',
          borderBottom: 1,
          borderColor: 'divider',
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
          ml: { md: `${DRAWER_WIDTH}px` },
        }}
      >
        <Toolbar sx={{ gap: 1 }}>
          {isMobile && (
            <IconButton
              edge="start"
              color="inherit"
              aria-label={t('navigation.menu')}
              onClick={() => setMobileOpen(true)}
            >
              <MenuIcon />
            </IconButton>
          )}

          <Typography variant="body2" color="text.primary" sx={{ fontWeight: 600 }}>
            {pageTitle || t('navigation.home')}
          </Typography>

          <Box sx={{ flex: 1 }} />

          {user && (
            <Chip
              label={user.display_name || user.email}
              size="small"
              variant="outlined"
              sx={{ maxWidth: { xs: 120, sm: 220 } }}
            />
          )}
          {user?.is_admin && (
            <Chip label="Admin" size="small" color="primary" sx={{ height: 20, fontSize: '0.65rem' }} />
          )}

          <FormControl size="small" sx={{ minWidth: 180, display: { xs: 'none', sm: 'flex' } }}>
            <InputLabel id="tenant-select-label" shrink>{t('header.tenant')}</InputLabel>
            <Select
              labelId="tenant-select-label"
              label={t('header.tenant')}
              notched
              value={selectedTenant || ''}
              onChange={(event) => handleTenantChange(event.target.value)}
              displayEmpty
              disabled={isLoadingTenants || isSwitching}
              inputProps={{ 'aria-label': t('header.tenant') }}
              renderValue={(value) => availableTenants.find((tenant) => tenant.id === value)?.name || value || t('header.tenant_none')}
              startAdornment={<BusinessIcon sx={{ fontSize: 18, mr: 0.5, color: 'text.secondary' }} />}
              sx={{ fontSize: '0.85rem' }}
            >
              {isLoadingTenants ? (
                <MenuItem disabled><em>{t('header.tenant_loading')}</em></MenuItem>
              ) : (
                availableTenants.map((tenant) => <MenuItem key={tenant.id} value={tenant.id}>{tenant.name}</MenuItem>)
              )}
            </Select>
          </FormControl>

          <FormControl size="small" sx={{ minWidth: 110, display: { xs: 'none', sm: 'flex' } }}>
            <InputLabel id="theme-select-label">{t('header.theme')}</InputLabel>
            <Select
              labelId="theme-select-label"
              value={themeName}
              label={t('header.theme')}
              onChange={(event) => setThemeName(event.target.value as ThemeName)}
              sx={{ fontSize: '0.85rem' }}
            >
              {themeOptions.map((option) => (
                <MenuItem key={option.value} value={option.value}>{t(`header.theme_${option.value}`)}</MenuItem>
              ))}
            </Select>
          </FormControl>

          <NotificationBell />
          <LanguageSwitcher variant="outlined" size="small" />
        </Toolbar>
        <Snackbar
          open={switchFailed}
          autoHideDuration={6000}
          onClose={() => setSwitchFailed(false)}
          anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
        >
          <Alert severity="error" variant="filled" onClose={() => setSwitchFailed(false)}>{t('header.tenant_switch_failed')}</Alert>
        </Snackbar>
      </AppBar>

      <Box component="nav" sx={{ width: { md: DRAWER_WIDTH }, flexShrink: { md: 0 } }}>
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={closeMobile}
          ModalProps={{ keepMounted: true }}
          sx={{
            display: { xs: 'block', md: 'none' },
            '& .MuiDrawer-paper': { width: DRAWER_WIDTH, boxSizing: 'border-box' },
          }}
        >
          {drawer}
        </Drawer>
        <Drawer
          variant="permanent"
          open
          sx={{
            display: { xs: 'none', md: 'block' },
            '& .MuiDrawer-paper': {
              width: DRAWER_WIDTH,
              boxSizing: 'border-box',
              borderRight: 1,
              borderColor: 'divider',
            },
          }}
        >
          {drawer}
        </Drawer>
      </Box>

      <Box
        sx={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
        }}
      >
        <Toolbar />
        <Box component="main" sx={{ flex: 1, py: 4, backgroundColor: 'background.default' }}>
          <Container maxWidth="xl">{children}</Container>
        </Box>
        <Box
          component="footer"
          sx={{
            py: 2,
            textAlign: 'center',
            borderTop: 1,
            borderColor: 'divider',
            backgroundColor: 'background.paper',
          }}
        >
          <Typography variant="caption" color="text.secondary">
            PrivacyManager · PramaIA Platform © {new Date().getFullYear()}
          </Typography>
        </Box>
      </Box>
    </Box>
  );
};
