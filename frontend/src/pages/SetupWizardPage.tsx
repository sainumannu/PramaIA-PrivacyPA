import React, { useState, useEffect } from 'react';
import {
  Box, Stepper, Step, StepLabel, Button, TextField,
  Typography, Paper, Alert, RadioGroup, FormControlLabel,
  Radio, InputAdornment, IconButton, Divider, Chip, Container
} from '@mui/material';
import {
  CloudSync as CloudSyncIcon,
  Edit as EditIcon,
  VpnKey as VpnKeyIcon,
  Cloud as CloudIcon,
  Storage as StorageIcon,
  Visibility as VisibilityIcon,
  VisibilityOff as VisibilityOffIcon,
  ContentCopy as ContentCopyIcon,
  Refresh as RefreshIcon,
  CheckCircle as CheckCircleIcon
} from '@mui/icons-material';
import axios from 'axios';
import { ErrorBanner } from '../components/ErrorBanner';

interface SetupData {
  setup_method: 'fetch' | 'manual';
  // Fetch mode
  app_id: string;
  app_secret: string;
  // Manual mode
  jwt_secret: string;
  // Common
  portal_url: string;
  database_url: string;
}

interface SetupStatus {
  is_configured: boolean;
  has_service_credentials: boolean;
}

const SetupWizardPage: React.FC = () => {
  const [activeStep, setActiveStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [copied, setCopied] = useState(false);
  
  const [setupData, setSetupData] = useState<SetupData>({
    setup_method: 'fetch',
    app_id: 'PramaIA-PrivacyManager',
    app_secret: '',
    jwt_secret: '',
    portal_url: 'http://localhost:3080',
    database_url: 'sqlite:///./PramaIA-PrivacyManager.db'
  });

  const steps = ['Metodo Setup', 'Configurazione', 'Conferma & Salva'];

  // ============ APP GIÀ CONFIGURATA: RIALLINEAMENTO ============
  // Il wizard resta raggiungibile su /setup anche dopo il primo setup: serve quando il
  // Portal ha cambiato JWT secret e non si riesce più a entrare nell'app.
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [resyncing, setResyncing] = useState(false);
  const [resyncResult, setResyncResult] = useState<{ severity: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    axios.get('/api/setup/check')
      .then((response) => setSetupStatus(response.data))
      .catch(() => setSetupStatus(null));
  }, []);

  const handleResync = async () => {
    setResyncing(true);
    setResyncResult(null);
    try {
      const response = await axios.post('/api/setup/resync');
      setResyncResult({
        severity: 'success',
        text: response.data.changed
          ? "JWT Secret aggiornato dal Portal: puoi tornare all'app e rifare il login."
          : 'Il JWT Secret è già allineato al Portal.',
      });
    } catch (err: any) {
      setResyncResult({ severity: 'error', text: err.response?.data?.detail || err.message || 'Errore durante il riallineamento' });
    } finally {
      setResyncing(false);
    }
  };

  // ============ GENERATE SECRET ============
  useEffect(() => {
    // Genera solo il JWT secret per modalità manuale
    if (setupData.setup_method === 'manual' && !setupData.jwt_secret) {
      generateSecret('jwt_secret');
    }
  }, [setupData.setup_method]);

  const generateSecret = async (field: 'app_secret' | 'jwt_secret') => {
    try {
      const response = await axios.post('/api/setup/generate-secret');
      setSetupData(prev => ({ ...prev, [field]: response.data.jwt_secret }));
    } catch (err) {
      console.error('Errore generazione secret:', err);
    }
  };

  // ============ VALIDATION ============
  const canProceed = (): boolean => {
    switch (activeStep) {
      case 0:
        return true;
      case 1:
        if (setupData.setup_method === 'fetch') {
          return (
            setupData.app_id.trim().length >= 3 &&
            setupData.app_secret.length >= 32 &&
            setupData.portal_url !== ''
          );
        } else {
          return setupData.jwt_secret.length >= 32 && setupData.portal_url !== '';
        }
      case 2:
        return true;
      default:
        return false;
    }
  };

  // ============ NAVIGATION ============
  const handleNext = () => {
    if (canProceed()) {
      setActiveStep((prev) => prev + 1);
      setError('');
    }
  };

  const handleBack = () => {
    setActiveStep((prev) => prev - 1);
    setError('');
  };

  // ============ SAVE ============
  const handleSave = async () => {
    setLoading(true);
    setError('');

    try {
      if (setupData.setup_method === 'fetch') {
        // Fetch automatico da Portal
        await axios.post('/api/setup/fetch-from-portal', {
          app_id: setupData.app_id,
          app_secret: setupData.app_secret,
          portal_url: setupData.portal_url,
          database_url: setupData.database_url
        });
      } else {
        // Setup manuale
        await axios.post('/api/setup/initialize', {
          jwt_secret: setupData.jwt_secret,
          portal_url: setupData.portal_url,
          database_url: setupData.database_url
        });
      }

      setSuccess(true);
      
      // Attendi 3 secondi poi ricarica per applicare nuova config
      setTimeout(() => {
        window.location.reload();
      }, 3000);
      
    } catch (err: any) {
      const errorMsg = err.response?.data?.detail || err.message || 'Errore durante il setup';
      setError(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  // ============ COPY TO CLIPBOARD ============
  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // ============ RENDER STEPS ============
  const renderStepContent = (step: number) => {
    switch (step) {
      case 0:
        // Step 1: Scegli metodo
        return (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <Alert severity="info">
              <Typography variant="body2">
                Scegli come configurare il JWT Secret per l'autenticazione con il Portal.
              </Typography>
            </Alert>

            <RadioGroup
              value={setupData.setup_method}
              onChange={(e) => setSetupData({ ...setupData, setup_method: e.target.value as 'fetch' | 'manual' })}
            >
              <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
                <FormControlLabel
                  value="fetch"
                  control={<Radio />}
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                      <CloudSyncIcon color="primary" />
                      <Box sx={{ flex: 1 }}>
                        <Typography variant="body1" fontWeight={600}>
                          Fetch Automatico dal Portal (Consigliato)
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          Il JWT Secret viene recuperato automaticamente dal Portal. Zero rischio di errori.
                        </Typography>
                      </Box>
                      <Chip label="Consigliato" color="primary" size="small" />
                    </Box>
                  }
                  sx={{ m: 0, width: '100%' }}
                />
              </Paper>

              <Paper variant="outlined" sx={{ p: 2 }}>
                <FormControlLabel
                  value="manual"
                  control={<Radio />}
                  label={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <EditIcon color="secondary" />
                      <Box>
                        <Typography variant="body1" fontWeight={600}>
                          Configurazione Manuale
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          Per ambienti di sviluppo o situazioni offline. Richiede copia manuale del secret.
                        </Typography>
                      </Box>
                    </Box>
                  }
                  sx={{ m: 0 }}
                />
              </Paper>
            </RadioGroup>
          </Box>
        );

      case 1:
        // Step 2: Configurazione
        if (setupData.setup_method === 'fetch') {
          return (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <Alert severity="success">
                <Typography variant="body2">
                  ✅ <strong>Metodo Automatico:</strong> Il JWT Secret verrà scaricato dal Portal.
                </Typography>
              </Alert>

              <Alert severity="info">
                <Typography variant="body2" fontWeight={600} gutterBottom>
                  📋 Coordinamento con Admin Portal
                </Typography>
                <Typography variant="body2" sx={{ mb: 1 }}>
                  1. L'<strong>X-App-ID</strong> deve coincidere esattamente con quello registrato nel Portal<br />
                  2. L'admin genera e ti fornisce il <strong>App Secret</strong><br />
                  3. Inserisci l'App Secret nel campo qui sotto
                </Typography>
              </Alert>

              <TextField
                fullWidth
                label="X-App-ID (deve coincidere col Portal)"
                value={setupData.app_id}
                onChange={(e) => setSetupData({ ...setupData, app_id: e.target.value.trim() })}
                helperText="ID esatto dell'app in Portal → Applicazioni. È case-sensitive."
                InputProps={{
                  sx: { fontFamily: 'monospace' },
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton
                        size="small"
                        onClick={() => copyToClipboard(setupData.app_id)}
                        color="primary"
                      >
                        <ContentCopyIcon fontSize="small" />
                      </IconButton>
                    </InputAdornment>
                  )
                }}
              />

              <TextField
                fullWidth
                label="Portal URL"
                value={setupData.portal_url}
                onChange={(e) => setSetupData({ ...setupData, portal_url: e.target.value })}
                placeholder="http://localhost:3080"
                helperText="URL del Portal PramaIA"
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <CloudIcon color="primary" />
                    </InputAdornment>
                  )
                }}
              />

              <TextField
                fullWidth
                label="App Secret (fornito dall'admin Portal)"
                value={setupData.app_secret}
                onChange={(e) => setSetupData({ ...setupData, app_secret: e.target.value })}
                type={showSecret ? 'text' : 'password'}
                helperText={`Inserisci il secret fornito dall'admin Portal (${setupData.app_secret.length} caratteri)`}
                placeholder="Incolla qui l'App Secret fornito dall'admin Portal"
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <VpnKeyIcon color="primary" />
                    </InputAdornment>
                  ),
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton onClick={() => setShowSecret(!showSecret)} edge="end">
                        {showSecret ? <VisibilityOffIcon /> : <VisibilityIcon />}
                      </IconButton>
                    </InputAdornment>
                  )
                }}
              />

              <TextField
                fullWidth
                label="Database URL"
                value={setupData.database_url}
                onChange={(e) => setSetupData({ ...setupData, database_url: e.target.value })}
                placeholder="sqlite:///./PramaIA-PrivacyManager.db"
                helperText="Lascia il default per SQLite locale"
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <StorageIcon color="primary" />
                    </InputAdornment>
                  )
                }}
              />
            </Box>
          );
        } else {
          return (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              <Alert severity="warning">
                <Typography variant="body2">
                  ⚠️ <strong>Modalità Manuale:</strong> Dovrai inserire manualmente lo stesso JWT Secret nel Portal.
                </Typography>
              </Alert>

              <TextField
                fullWidth
                label="JWT Secret"
                value={setupData.jwt_secret}
                onChange={(e) => setSetupData({ ...setupData, jwt_secret: e.target.value })}
                type={showSecret ? 'text' : 'password'}
                placeholder="Genera o incolla JWT secret (minimo 32 caratteri)"
                helperText={`Lunghezza: ${setupData.jwt_secret.length} caratteri (minimo 32)`}
                error={setupData.jwt_secret.length > 0 && setupData.jwt_secret.length < 32}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <VpnKeyIcon color="primary" />
                    </InputAdornment>
                  ),
                  endAdornment: (
                    <InputAdornment position="end">
                      <IconButton onClick={() => copyToClipboard(setupData.jwt_secret)} edge="end" size="small">
                        <ContentCopyIcon fontSize="small" />
                      </IconButton>
                      <IconButton onClick={() => setShowSecret(!showSecret)} edge="end">
                        {showSecret ? <VisibilityOffIcon /> : <VisibilityIcon />}
                      </IconButton>
                    </InputAdornment>
                  )
                }}
              />

              <Button
                variant="outlined"
                startIcon={<RefreshIcon />}
                onClick={() => generateSecret('jwt_secret')}
              >
                Genera Nuovo Secret
              </Button>

              <Alert severity="error">
                <Typography variant="body2" fontWeight={600}>
                  📋 IMPORTANTE: Salva questo secret e inseriscilo anche nel Portal!
                </Typography>
              </Alert>

              <TextField
                fullWidth
                label="Portal URL"
                value={setupData.portal_url}
                onChange={(e) => setSetupData({ ...setupData, portal_url: e.target.value })}
                placeholder="http://localhost:3080"
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <CloudIcon color="primary" />
                    </InputAdornment>
                  )
                }}
              />
            </Box>
          );
        }

      case 2:
        // Step 3: Conferma
        return (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <Alert severity="success">
              <Typography variant="body2" fontWeight={600}>
                ✅ Configurazione completata! Rivedi i dati prima di salvare.
              </Typography>
            </Alert>

            <Paper variant="outlined" sx={{ p: 3 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                {setupData.setup_method === 'fetch' ? (
                  <>
                    <CloudSyncIcon fontSize="small" color="primary" />
                    <Typography variant="subtitle2" color="primary" fontWeight={600}>
                      Setup Automatico (Fetch da Portal)
                    </Typography>
                  </>
                ) : (
                  <>
                    <EditIcon fontSize="small" color="secondary" />
                    <Typography variant="subtitle2" color="secondary" fontWeight={600}>
                      Setup Manuale
                    </Typography>
                  </>
                )}
              </Box>

              <Divider sx={{ mb: 2 }} />

              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Portal URL
              </Typography>
              <Typography variant="body2" sx={{ mb: 2 }}>
                {setupData.portal_url}
              </Typography>

              <Divider sx={{ my: 2 }} />

              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                X-App-ID
              </Typography>
              <Typography variant="body2" sx={{ mb: 2, fontFamily: 'monospace', fontSize: '0.85rem', color: 'primary.main', fontWeight: 600 }}>
                {setupData.app_id}
              </Typography>

              <Divider sx={{ my: 2 }} />

              <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                Database URL
              </Typography>
              <Typography variant="body2" sx={{ mb: 2, fontFamily: 'monospace', fontSize: '0.85rem' }}>
                {setupData.database_url}
              </Typography>

              {setupData.setup_method === 'manual' && (
                <>
                  <Divider sx={{ my: 2 }} />
                  <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                    JWT Secret (copia nel Portal!)
                  </Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace', wordBreak: 'break-all', flex: 1 }}>
                      {showSecret ? setupData.jwt_secret : '••••••••••••••••••••••••••••••••'}
                    </Typography>
                    <IconButton size="small" onClick={() => copyToClipboard(setupData.jwt_secret)}>
                      <ContentCopyIcon fontSize="small" />
                    </IconButton>
                    <IconButton size="small" onClick={() => setShowSecret(!showSecret)}>
                      {showSecret ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                    </IconButton>
                  </Box>
                </>
              )}

              {setupData.setup_method === 'fetch' && (
                <>
                  <Divider sx={{ my: 2 }} />
                  <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                    App Secret (per registrazione Portal)
                  </Typography>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="body2" sx={{ fontFamily: 'monospace', wordBreak: 'break-all', flex: 1 }}>
                      {showSecret ? setupData.app_secret : '••••••••••••••••••••••••••••••••'}
                    </Typography>
                    <IconButton size="small" onClick={() => copyToClipboard(setupData.app_secret)}>
                      <ContentCopyIcon fontSize="small" />
                    </IconButton>
                    <IconButton size="small" onClick={() => setShowSecret(!showSecret)}>
                      {showSecret ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                    </IconButton>
                  </Box>
                </>
              )}
            </Paper>

            {copied && (
              <Alert severity="success">
                Copiato negli appunti!
              </Alert>
            )}

            <ErrorBanner message={error} onClose={() => setError('')} />
          </Box>
        );

      default:
        return null;
    }
  };

  // ============ SUCCESS SCREEN ============
  if (success) {
    return (
      <Container maxWidth="sm">
        <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Paper sx={{ p: 4, textAlign: 'center', width: '100%' }}>
            <CheckCircleIcon color="success" sx={{ fontSize: 80, mb: 2 }} />
            <Typography variant="h5" gutterBottom>Setup Completato!</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Configurazione salvata con successo.
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Ricaricamento app in corso...
            </Typography>
          </Paper>
        </Box>
      </Container>
    );
  }

  // ============ MAIN WIZARD ============
  return (
    <Container maxWidth="md">
      <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', py: 4 }}>
        <Paper elevation={3} sx={{ p: 4, width: '100%' }}>
          <Typography variant="h4" gutterBottom align="center" sx={{ mb: 1 }}>
            🛡️ Setup Iniziale
          </Typography>
          <Typography variant="body2" color="text.secondary" align="center" sx={{ mb: 4 }}>
            PrivacyManager
          </Typography>

          {setupStatus?.is_configured && (
            <Alert severity="warning" sx={{ mb: 4 }}>
              <Typography variant="body2" sx={{ mb: 1.5 }}>
                L'app è già configurata. Se i login non funzionano più perché il Portal ha cambiato il JWT
                Secret (es. dopo il ripristino di un backup), riallineala al Portal.
              </Typography>
              {setupStatus.has_service_credentials ? (
                <Button
                  variant="contained"
                  size="small"
                  startIcon={<CloudSyncIcon />}
                  onClick={handleResync}
                  disabled={resyncing}
                >
                  {resyncing ? 'Riallineamento...' : 'Riallinea con il Portal'}
                </Button>
              ) : (
                <Typography variant="body2">
                  Mancano le credenziali di servizio dell'app: prosegui con il Fetch Automatico e inserisci
                  l'App Secret generato nel Portal.
                </Typography>
              )}
              {resyncResult && (
                <Alert severity={resyncResult.severity} sx={{ mt: 1.5 }}>
                  {resyncResult.text}
                </Alert>
              )}
              <Typography variant="caption" display="block" sx={{ mt: 1.5 }}>
                Se nel Portal è stato rigenerato anche l'App Secret, prosegui con il Fetch Automatico e
                inserisci quello nuovo: verranno aggiornati solo i segreti, non il database né gli URL.
              </Typography>
            </Alert>
          )}

          <Stepper activeStep={activeStep} sx={{ mb: 4 }}>
            {steps.map((label) => (
              <Step key={label}>
                <StepLabel>{label}</StepLabel>
              </Step>
            ))}
          </Stepper>

          <Box sx={{ mb: 4 }}>
            {renderStepContent(activeStep)}
          </Box>

          <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
            <Button
              disabled={activeStep === 0}
              onClick={handleBack}
            >
              Indietro
            </Button>

            {activeStep === steps.length - 1 ? (
              <Button
                variant="contained"
                onClick={handleSave}
                disabled={loading || !canProceed()}
              >
                {loading ? 'Salvataggio...' : 'Salva e Completa Setup'}
              </Button>
            ) : (
              <Button
                variant="contained"
                onClick={handleNext}
                disabled={!canProceed()}
              >
                Avanti
              </Button>
            )}
          </Box>
        </Paper>
      </Box>
    </Container>
  );
};

export default SetupWizardPage;
