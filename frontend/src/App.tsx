import React, { Suspense, useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { CircularProgress, Box } from '@mui/material';
import { authGuard, redirectToPortal } from './services/authGuard';
import { loadConfig } from './services/runtimeConfig';
import HomePage from './pages/HomePage';
import SettingsPage from './pages/SettingsPage';
import SetupWizardPage from './pages/SetupWizardPage';
import OrganizationPage from './pages/OrganizationPage';
import RegisterPage from './pages/RegisterPage';
import AssetsPage from './pages/AssetsPage';
import PartiesPage from './pages/PartiesPage';
import DocumentsPage from './pages/DocumentsPage';
import TasksPage from './pages/TasksPage';
import ImportPage from './pages/ImportPage';
import ApprovalsPage from './pages/ApprovalsPage';
import PolicyPage from './pages/PolicyPage';
import SupplyChainPage from './pages/SupplyChainPage';
import FindingsPage from './pages/FindingsPage';
import NoticesPage from './pages/NoticesPage';
import { AuditsPage, BreachesPage, CorrectiveActionsPage, LegalReferencesPage, RequestsPage } from './pages/WorkflowPages';
import { AuthorizationProfilesPage, AuthorizationsPage, TrainingAssignmentsPage, TrainingCoursesPage } from './pages/PeoplePages';
import { AnnualReportPage, DpoWorkspacePage, OpinionsPage } from './pages/DpoPages';
import { ProjectsPage } from './pages/ProjectsPage';
import { CustomFieldsPage, TagsPage } from './pages/AnnotationPages';
import QueryBuilderPage from './pages/QueryBuilderPage';
import WebhooksPage from './pages/WebhooksPage';
import SearchPage from './pages/SearchPage';
import AuditPage from './pages/AuditPage';
import CatalogsPage from './pages/CatalogsPage';
import DpiaPage from './pages/DpiaPage';
import TransfersPage from './pages/TransfersPage';
import RisksPage from './pages/RisksPage';
import ErrorBoundary from './components/ErrorBoundary';

// Inizializza i18n
import './i18n/i18n';

function PrivateRoute({ children }: { children: React.ReactNode }) {
  if (!authGuard.isAuthenticated()) {
    // Sessione assente/non valida: mostra un messaggio prima di reindirizzare al Portal
    redirectToPortal('Sessione non trovata o non valida. Effettua nuovamente l\'accesso dal Portal.');
    return null;
  }
  return <>{children}</>;
}

// Loading fallback per Suspense (i18n lazy loading)
const LoadingFallback = () => (
  <Box 
    display="flex" 
    justifyContent="center" 
    alignItems="center" 
    minHeight="100vh"
  >
    <CircularProgress />
  </Box>
);

export default function App() {
  // URL del Portal letto da /api/config prima delle rotte: serve al redirect
  // di login. Il wizard di setup resta raggiungibile esplicitamente su /setup
  // (non più come schermata che intercetta l'intera app): gli endpoint che
  // scrive restano protetti lato backend (solo localhost, vedi
  // backend/routers/setup_router.py) finché non verrà rimosso in un secondo
  // momento, a favore della sola configurazione a tempo di installazione.
  const [configReady, setConfigReady] = useState(false);

  useEffect(() => {
    loadConfig().finally(() => setConfigReady(true));
  }, []);

  if (!configReady) {
    return <LoadingFallback />;
  }

  return (
    <ErrorBoundary>
      <Suspense fallback={<LoadingFallback />}>
        <BrowserRouter>
          <Routes>
            <Route path="/setup" element={<SetupWizardPage />} />
            <Route
              path="/"
              element={
                <PrivateRoute>
                  <HomePage />
                </PrivateRoute>
              }
            />
            <Route
              path="/settings"
              element={
                <PrivateRoute>
                  <SettingsPage />
                </PrivateRoute>
              }
            />
            <Route
              path="/organizzazione"
              element={
                <PrivateRoute>
                  <OrganizationPage />
                </PrivateRoute>
              }
            />
            <Route
              path="/registro"
              element={
                <PrivateRoute>
                  <RegisterPage />
                </PrivateRoute>
              }
            />
            <Route path="/sistemi" element={<PrivateRoute><AssetsPage /></PrivateRoute>} />
            <Route path="/fornitori" element={<PrivateRoute><PartiesPage /></PrivateRoute>} />
            <Route path="/documenti" element={<PrivateRoute><DocumentsPage /></PrivateRoute>} />
            <Route path="/task" element={<PrivateRoute><TasksPage /></PrivateRoute>} />
            <Route path="/import" element={<PrivateRoute><ImportPage /></PrivateRoute>} />
            <Route path="/approvazioni" element={<PrivateRoute><ApprovalsPage /></PrivateRoute>} />
            <Route path="/policy" element={<PrivateRoute><PolicyPage /></PrivateRoute>} />
            <Route path="/filiera" element={<PrivateRoute><SupplyChainPage /></PrivateRoute>} />
            <Route path="/verifiche" element={<PrivateRoute><FindingsPage /></PrivateRoute>} />
            <Route path="/informative" element={<PrivateRoute><NoticesPage /></PrivateRoute>} />
            <Route path="/riferimenti" element={<PrivateRoute><LegalReferencesPage /></PrivateRoute>} />
            <Route path="/azioni" element={<PrivateRoute><CorrectiveActionsPage /></PrivateRoute>} />
            <Route path="/tag" element={<PrivateRoute><TagsPage /></PrivateRoute>} />
            <Route path="/interrogazioni" element={<PrivateRoute><QueryBuilderPage /></PrivateRoute>} />
            <Route path="/webhook" element={<PrivateRoute><WebhooksPage /></PrivateRoute>} />
            <Route path="/campi-custom" element={<PrivateRoute><CustomFieldsPage /></PrivateRoute>} />
            <Route path="/progetti" element={<PrivateRoute><ProjectsPage /></PrivateRoute>} />
            <Route path="/formazione" element={<PrivateRoute><TrainingCoursesPage /></PrivateRoute>} />
            <Route path="/formazione/assegnazioni" element={<PrivateRoute><TrainingAssignmentsPage /></PrivateRoute>} />
            <Route path="/autorizzazioni" element={<PrivateRoute><AuthorizationsPage /></PrivateRoute>} />
            <Route path="/profili-autorizzazione" element={<PrivateRoute><AuthorizationProfilesPage /></PrivateRoute>} />
            <Route path="/dpo" element={<PrivateRoute><DpoWorkspacePage /></PrivateRoute>} />
            <Route path="/pareri-dpo" element={<PrivateRoute><OpinionsPage /></PrivateRoute>} />
            <Route path="/relazione-annuale" element={<PrivateRoute><AnnualReportPage /></PrivateRoute>} />
            <Route path="/violazioni" element={<PrivateRoute><BreachesPage /></PrivateRoute>} />
            <Route path="/richieste" element={<PrivateRoute><RequestsPage /></PrivateRoute>} />
            <Route path="/audit-programmati" element={<PrivateRoute><AuditsPage /></PrivateRoute>} />
            <Route path="/ricerca" element={<PrivateRoute><SearchPage /></PrivateRoute>} />
            <Route path="/audit" element={<PrivateRoute><AuditPage /></PrivateRoute>} />
            <Route path="/cataloghi" element={<PrivateRoute><CatalogsPage /></PrivateRoute>} />
            <Route path="/dpia" element={<PrivateRoute><DpiaPage /></PrivateRoute>} />
            <Route path="/trasferimenti" element={<PrivateRoute><TransfersPage /></PrivateRoute>} />
            <Route path="/rischi" element={<PrivateRoute><RisksPage /></PrivateRoute>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </Suspense>
    </ErrorBoundary>
  );
}

