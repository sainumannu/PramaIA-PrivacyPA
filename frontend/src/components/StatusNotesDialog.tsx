import React, { useEffect, useState } from 'react';
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography } from '@mui/material';
import { useTranslation } from '../i18n/useTranslation';

interface Props {
  open: boolean;
  title: string;
  hint?: string;
  required?: boolean;
  error?: string | null;
  busy?: boolean;
  confirmLabel?: string;
  onConfirm: (notes: string) => void;
  onClose: () => void;
}

// Nota da allegare a un invio in revisione o a una decisione di approvazione (§165).
export const StatusNotesDialog: React.FC<Props> = ({
  open, title, hint, required, error, busy, confirmLabel, onConfirm, onClose,
}) => {
  const { t } = useTranslation('privacypa');
  const [notes, setNotes] = useState('');

  useEffect(() => {
    if (open) setNotes('');
  }, [open]);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{title}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        {hint && <Typography variant="body2" color="text.secondary">{hint}</Typography>}
        {error && <Alert severity="error">{error}</Alert>}
        <TextField
          label={t('status_notes.notes')}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          multiline
          minRows={3}
          required={required}
          autoFocus
          inputProps={{ maxLength: 4000 }}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t('common.cancel')}</Button>
        <Button
          variant="contained"
          disabled={busy || (required && !notes.trim())}
          onClick={() => onConfirm(notes.trim())}
        >
          {confirmLabel || t('status_notes.confirm')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default StatusNotesDialog;
