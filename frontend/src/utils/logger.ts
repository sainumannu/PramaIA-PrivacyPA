/**
 * Frontend logger for PrivacyManager
 * 
 * Provides a standardized logging interface with:
 * - Different log levels (debug, info, warn, error)
 * - Automatic timestamp
 * - Styled console output (colors, icons)
 * - Production mode (disables debug/info logs)
 * - API call logging
 * - Error boundary integration
 */

type LogLevel = 'debug' | 'info' | 'warn' | 'error';

interface LogEntry {
  level: LogLevel;
  message: string;
  data?: any;
  timestamp: string;
}

class Logger {
  private isDevelopment: boolean;
  private appName: string = 'PrivacyManager';
  
  // Styles for console output
  private styles = {
    debug: 'color: #6366f1; font-weight: bold',
    info: 'color: #10b981; font-weight: bold',
    warn: 'color: #f59e0b; font-weight: bold',
    error: 'color: #ef4444; font-weight:bold',
  };
  
  // Icons for log levels
  private icons = {
    debug: '🔍',
    info: 'ℹ️',
    warn: '⚠️',
    error: '❌',
  };
  
  constructor() {
    this.isDevelopment = process.env.NODE_ENV === 'development';
  }
  
  /**
   * Log debug message (only in development)
   */
  debug(message: string, data?: any): void {
    if (!this.isDevelopment) return;
    this.log('debug', message, data);
  }
  
  /**
   * Log info message
   */
  info(message: string, data?: any): void {
    if (!this.isDevelopment) return;
    this.log('info', message, data);
  }
  
  /**
   * Log warning message
   */
  warn(message: string, data?: any): void {
    this.log('warn', message, data);
  }
  
  /**
   * Log error message
   */
  error(message: string, error?: any): void {
    this.log('error', message, error);
    
    // Log full error object in console for debugging
    if (error) {
      if (error instanceof Error) {
        console.error('Error details:', {
          name: error.name,
          message: error.message,
          stack: error.stack,
        });
      } else {
        console.error('Error details:', error);
      }
    }
  }
  
  /**
   * Log API request
   */
  apiRequest(method: string, url: string, data?: any): void {
    this.debug(`→ API ${method} ${url}`, data);
  }
  
  /**
   * Log API response
   */
  apiResponse(method: string, url: string, status: number, duration?: number): void {
    const durationStr = duration ? ` (${duration}ms)` : '';
    const level: LogLevel = status >= 400 ? 'error' : 'debug';
    this.log(level, `← API ${method} ${url} | ${status}${durationStr}`);
  }
  
  /**
   * Log API error
   */
  apiError(method: string, url: string, error: any): void {
    this.error(`✗ API ${method} ${url} failed`, error);
  }
  
  /**
   * Core logging function
   */
  private log(level: LogLevel, message: string, data?: any): void {
    const timestamp = new Date().toISOString();
    const icon = this.icons[level];
    const style = this.styles[level];
    
    const formattedMessage = `[${this.appName}] ${icon} ${message}`;
    
    // Console output with styling
    if (data !== undefined) {
      console.log(`%c${formattedMessage}`, style, data);
    } else {
      console.log(`%c${formattedMessage}`, style);
    }
    
    // In production, you might want to send errors to a monitoring service
    if (level === 'error' && !this.isDevelopment) {
      this.sendToMonitoring({ level, message, data, timestamp });
    }
  }
  
  /**
   * Send error to monitoring service (placeholder)
   * In production, integrate with Sentry, LogRocket, etc.
   */
  private sendToMonitoring(entry: LogEntry): void {
    // TODO: Implement monitoring service integration
    // Example: Sentry.captureMessage(entry.message, { level: entry.level, extra: entry.data });
  }
}

// Export singleton instance
export const logger = new Logger();

// Export type for external use
export type { LogLevel };
