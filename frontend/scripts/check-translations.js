#!/usr/bin/env node
/**
 * Script per verificare completezza traduzioni.
 * 
 * Confronta file traduzioni IT e EN e segnala chiavi mancanti.
 * 
 * Usage:
 *   node scripts/check-translations.js
 * 
 * Exit code:
 *   0 = tutte le traduzioni complete
 *   1 = traduzioni mancanti rilevate
 */

const fs = require('fs');
const path = require('path');

// Colori console
const RED = '\x1b[31m';
const GREEN = '\x1b[32m';
const YELLOW = '\x1b[33m';
const RESET = '\x1b[0m';

// Lingue supportate
const LANGUAGES = ['it', 'en'];

// Namespace da verificare
const NAMESPACES = ['common', 'license', 'settings', 'auth', 'privacypa'];

/**
 * Appiattisce oggetto JSON nested in chiavi dot-notation.
 * 
 * @example
 * { a: { b: 'value' } } → { 'a.b': 'value' }
 */
function flattenObject(obj, prefix = '') {
  return Object.keys(obj).reduce((acc, key) => {
    const pre = prefix.length ? prefix + '.' : '';
    if (typeof obj[key] === 'object' && obj[key] !== null && !Array.isArray(obj[key])) {
      Object.assign(acc, flattenObject(obj[key], pre + key));
    } else {
      acc[pre + key] = obj[key];
    }
    return acc;
  }, {});
}

/**
 * Carica file traduzione.
 */
function loadTranslations(lang, namespace) {
  const filePath = path.join(__dirname, `../src/locales/${lang}/${namespace}.json`);
  
  if (!fs.existsSync(filePath)) {
    console.error(`${RED}✗ File not found: ${filePath}${RESET}`);
    return null;
  }
  
  try {
    const content = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(content);
  } catch (error) {
    console.error(`${RED}✗ Error parsing ${filePath}: ${error.message}${RESET}`);
    return null;
  }
}

/**
 * Verifica completezza traduzioni tra due lingue.
 */
function checkCompleteness(lang1, lang2, namespace) {
  const trans1 = loadTranslations(lang1, namespace);
  const trans2 = loadTranslations(lang2, namespace);
  
  if (!trans1 || !trans2) {
    return { valid: false };
  }
  
  const keys1 = Object.keys(flattenObject(trans1));
  const keys2 = Object.keys(flattenObject(trans2));
  
  const missingIn2 = keys1.filter(k => !keys2.includes(k));
  const missingIn1 = keys2.filter(k => !keys1.includes(k));
  
  return {
    valid: missingIn1.length === 0 && missingIn2.length === 0,
    missingIn1,
    missingIn2,
    totalKeys: keys1.length,
  };
}

/**
 * Verifica valori vuoti o placeholder.
 */
function checkEmptyValues(lang, namespace) {
  const trans = loadTranslations(lang, namespace);
  if (!trans) return [];
  
  const flattened = flattenObject(trans);
  const emptyKeys = [];
  
  for (const [key, value] of Object.entries(flattened)) {
    if (!value || value.toString().trim() === '' || value === 'TODO') {
      emptyKeys.push(key);
    }
  }
  
  return emptyKeys;
}

/**
 * Main.
 */
function main() {
  console.log('\n🌍 Translation Completeness Check\n');
  console.log('='.repeat(60));
  
  let hasErrors = false;
  
  for (const namespace of NAMESPACES) {
    console.log(`\n📦 Namespace: ${YELLOW}${namespace}${RESET}\n`);
    
    // Verifica completezza IT vs EN
    const result = checkCompleteness('it', 'en', namespace);
    
    if (!result.valid) {
      hasErrors = true;
    }
    
    if (result.valid === false) {
      console.log(`${RED}✗ Errore caricamento file${RESET}`);
      continue;
    }
    
    console.log(`  Total keys: ${result.totalKeys}`);
    
    // Missing in EN
    if (result.missingIn2.length > 0) {
      hasErrors = true;
      console.log(`\n  ${RED}✗ Missing in EN (${result.missingIn2.length}):${RESET}`);
      result.missingIn2.forEach(key => {
        console.log(`    - ${key}`);
      });
    }
    
    // Missing in IT
    if (result.missingIn1.length > 0) {
      hasErrors = true;
      console.log(`\n  ${RED}✗ Missing in IT (${result.missingIn1.length}):${RESET}`);
      result.missingIn1.forEach(key => {
        console.log(`    - ${key}`);
      });
    }
    
    // Valori vuoti IT
    const emptyIT = checkEmptyValues('it', namespace);
    if (emptyIT.length > 0) {
      hasErrors = true;
      console.log(`\n  ${YELLOW}⚠ Empty values in IT (${emptyIT.length}):${RESET}`);
      emptyIT.forEach(key => {
        console.log(`    - ${key}`);
      });
    }
    
    // Valori vuoti EN
    const emptyEN = checkEmptyValues('en', namespace);
    if (emptyEN.length > 0) {
      hasErrors = true;
      console.log(`\n  ${YELLOW}⚠ Empty values in EN (${emptyEN.length}):${RESET}`);
      emptyEN.forEach(key => {
        console.log(`    - ${key}`);
      });
    }
    
    if (result.valid && emptyIT.length === 0 && emptyEN.length === 0) {
      console.log(`  ${GREEN}✓ All translations complete${RESET}`);
    }
  }
  
  console.log('\n' + '='.repeat(60));
  
  if (hasErrors) {
    console.log(`\n${RED}❌ Translation check FAILED${RESET}\n`);
    process.exit(1);
  } else {
    console.log(`\n${GREEN}✅ All translations OK${RESET}\n`);
    process.exit(0);
  }
}

main();
