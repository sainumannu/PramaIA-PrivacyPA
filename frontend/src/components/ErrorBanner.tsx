import React from 'react';
import { Alert, Snackbar } from '@mui/material';

// Errore della pagina, sempre visibile in alto: anche sopra un dialog aperto. Prima l'avviso stava in cima alla pagina,
// coperto dal dialog, e un salvataggio fallito sembrava un «Salva» che non fa niente.
export const ErrorBanner: React.FC<{ message: string | null | undefined; onClose?: () => void }> = ({ message, onClose }) => (
  <Snackbar
    open={Boolean(message)}
    anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
    sx={{ zIndex: (theme) => theme.zIndex.modal + 1, top: { xs: 72, sm: 80 } }}
  >
    <Alert severity="error" variant="filled" onClose={onClose} sx={{ maxWidth: 720, whiteSpace: 'pre-line' }}>
      {message}
    </Alert>
  </Snackbar>
);

export default ErrorBanner;
