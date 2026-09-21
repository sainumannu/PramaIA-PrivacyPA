import React, { useEffect, useState } from 'react';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  Alert,
  Grid,
  CircularProgress,
} from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import { apiClient } from '../services/apiClient';
import { useTranslation } from '../i18n/useTranslation';

interface Organization {
  id: string;
  tenant_id: string;
  name: string;
  tax_code: string | null;
  ipa_code: string | null;
  pec: string | null;
  dpo_name: string | null;
  dpo_contact: string | null;
  active: boolean;
}

const emptyForm = {
  name: '',
  tax_code: '',
  ipa_code: '',
  pec: '',
  dpo_name: '',
  dpo_contact: '',
};

export const OrganizationEntityPanel: React.FC = () => {
  const { t } = useTranslation('privacypa');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [exists, setExists] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const org = await apiClient.get<Organization>('/api/privacypa/organization');
      setExists(true);
      setForm({
        name: org.name || '',
        tax_code: org.tax_code || '',
        ipa_code: org.ipa_code || '',
        pec: org.pec || '',
        dpo_name: org.dpo_name || '',
        dpo_contact: org.dpo_contact || '',
      });
    } catch (err: any) {
      if (err.status === 404) {
        setExists(false);
      } else {
        setError(err.message || t('common_error') || 'Errore di caricamento');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleSave = async () => {
    if (!form.name.trim()) {
      setError(t('organization.entity.name_required') || 'La denominazione è obbligatoria');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await apiClient.put('/api/privacypa/organization', {
        name: form.name,
        tax_code: form.tax_code || null,
        ipa_code: form.ipa_code || null,
        pec: form.pec || null,
        dpo_name: form.dpo_name || null,
        dpo_contact: form.dpo_contact || null,
      });
      setExists(true);
      setSuccess(t('organization.entity.saved') || 'Ente salvato con successo');
    } catch (err: any) {
      setError(err.message || t('organization.entity.save_failed') || 'Salvataggio fallito');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }

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
      {!exists && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {t('organization.entity.not_configured') || 'Ente non ancora configurato: compila il modulo e salva.'}
        </Alert>
      )}

      <Typography variant="h6" sx={{ mb: 2 }}>
        {t('organization.entity.title') || 'Anagrafica ente'}
      </Typography>

      <Grid container spacing={2}>
        <Grid item xs={12} sm={6}>
          <TextField
            label={t('organization.entity.name') || 'Denominazione'}
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            fullWidth
            required
          />
        </Grid>
        <Grid item xs={12} sm={6}>
          <TextField
            label={t('organization.entity.tax_code') || 'Codice fiscale'}
            value={form.tax_code}
            onChange={(e) => setForm({ ...form, tax_code: e.target.value })}
            fullWidth
          />
        </Grid>
        <Grid item xs={12} sm={6}>
          <TextField
            label={t('organization.entity.ipa_code') || 'Codice IPA'}
            value={form.ipa_code}
            onChange={(e) => setForm({ ...form, ipa_code: e.target.value })}
            fullWidth
          />
        </Grid>
        <Grid item xs={12} sm={6}>
          <TextField
            label={t('organization.entity.pec') || 'PEC'}
            value={form.pec}
            onChange={(e) => setForm({ ...form, pec: e.target.value })}
            fullWidth
          />
        </Grid>
        <Grid item xs={12} sm={6}>
          <TextField
            label={t('organization.entity.dpo_name') || 'Nome DPO'}
            value={form.dpo_name}
            onChange={(e) => setForm({ ...form, dpo_name: e.target.value })}
            fullWidth
          />
        </Grid>
        <Grid item xs={12} sm={6}>
          <TextField
            label={t('organization.entity.dpo_contact') || 'Contatto DPO'}
            value={form.dpo_contact}
            onChange={(e) => setForm({ ...form, dpo_contact: e.target.value })}
            fullWidth
          />
        </Grid>
      </Grid>

      <Box sx={{ mt: 3 }}>
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          onClick={handleSave}
          disabled={saving}
        >
          {t('organization.entity.save') || 'Salva'}
        </Button>
      </Box>
    </Paper>
  );
};

export default OrganizationEntityPanel;
