#!/usr/bin/env node
// brave_search_enhanced.js: Enhanced Brave Search Scraper
// Pyrmethus Protocol v34.0 | Termux-Optimized | Node.js Arcana
// Features: Brave Search Scraping, Enhanced Parsing, Robust Downloading, Termux Integration

'use strict';

const https = require('https');
const http = require('http');
const { URL } = require('url');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// ════════════════════════════════════════════════════════════════════════════════
// Chromatic Enchantment - ANSI Arcana for the Terminal Realm
// ════════════════════════════════════════════════════════════════════════════════
const COLOR = {
  MAGENTA: '\x1b[35m',
  RED: '\x1b[31m',
  GREEN: '\x1b[32m',
  BLUE: '\x1b[34m',
  CYAN: '\x1b[36m',
  YELLOW: '\x1b[33m',
  BRIGHT: '\x1b[1m',
  DIM: '\x1b[2m',
  RESET: '\x1b[0m'
};

// ════════════════════════════════════════════════════════════════════════════════
// Ether Configuration - Environment Bindings (Termux-Optimized)
// ════════════════════════════════════════════════════════════════════════════════
const LLM_OUTPUT = process.env.LLM_OUTPUT || '/dev/stdout';
const CACHE_DIR = process.env.CACHE_DIR || path.join(process.env.HOME || '.', '.cache/brave_search');
const DOWNLOAD_DIR = process.env.DOWNLOAD_DIR || path.join(process.env.HOME || '.', 'downloads/brave_search');
const USER_AGENT = process.env.USER_AGENT || 'Mozilla/5.0 (Linux; Android 10; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36';
const MAX_CONCURRENT_DOWNLOADS = 3;
const DEFAULT_TIMEOUT = 15000;

// Ensure Termux paths exist
const TERMUX_PATHS = [
  path.join(process.env.HOME || '.', 'storage/shared'),
  path.join(process.env.HOME || '.', 'storage/downloads')
];

// ════════════════════════════════════════════════════════════════════════════════
// Mystical Utilities - Enhanced with Termux Awareness
// ════════════════════════════════════════════════════════════════════════════════

/**
 * Inscribe error essence with Termux awareness
 */
function jsonError(msg, code = 1) {
  const payload = {
    success: false,
    message: msg,
    error_code: code,
    timestamp: new Date().toISOString()
  };

  const json = JSON.stringify(payload, null, 2);

  if (LLM_OUTPUT === '/dev/stdout') {
    console.error(COLOR.RED + json + COLOR.RESET);
  } else {
    try {
      const dir = path.dirname(LLM_OUTPUT);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(LLM_OUTPUT, json);
    } catch (err) {
      console.error(COLOR.RED + '// Failed to inscribe error to grimoire:' + COLOR.RESET, err.message);
      // Fallback to Termux toast notification
      try {
        execSync(`termux-toast -b red -c white "Error: ${msg.substring(0, 100)}"`);
      } catch (toastErr) {
        console.error(COLOR.DIM + '// Toast invocation failed' + COLOR.RESET);
      }
    }
  }
  process.exit(code);
}

/**
 * Unveil JSON data with Termux notifications
 */
function jsonOut(data) {
  const json = JSON.stringify(data, null, 2);

  if (LLM_OUTPUT === '/dev/stdout') {
    console.log(json);
  } else {
    try {
      const dir = path.dirname(LLM_OUTPUT);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(LLM_OUTPUT, json);
      console.log(COLOR.CYAN + `// Grimoire inscribed to: ${LLM_OUTPUT}` + COLOR.RESET);

      // Termux success notification
      const resultCount = data.results ? data.results.length : 0;
      execSync(`termux-toast -b green -c black "Search complete: ${resultCount} results"`);
    } catch (err) {
      console.error(COLOR.RED + `// Ether disturbance during inscription: ${err.message}` + COLOR.RESET);
      process.exit(1);
    }
  }
}

/**
 * Parse command line arguments with enhanced Termux support
 */
function parseArgs(argv) {
  const args = { _: [] };
  const aliases = {
    q: 'query',
    c: 'count',
    o: 'offset',
    l: 'language',
    t: 'timeout',
    d: 'download',
    r: 'include-raw',
    s: 'safe-search'
  };

  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];

    if (arg.startsWith('--')) {
      // Handle --option=value and --option value
      const equalIndex = arg.indexOf('=');
      let key, value;

      if (equalIndex > -1) {
        key = arg.slice(2, equalIndex);
        value = arg.slice(equalIndex + 1);
      } else {
        key = arg.slice(2);
        if (i + 1 < argv.length && !argv[i + 1].startsWith('-')) {
          value = argv[++i];
        } else {
          value = true;
        }
      }

      const camelKey = key.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      args[camelKey] = value;
    }
    else if (arg.startsWith('-') && arg.length > 1) {
      // Handle -abc flags and -a value
      const flags = arg.slice(1).split('');
      for (let j = 0; j < flags.length; j++) {
        const flag = flags[j];
        const alias = aliases[flag];

        if (alias) {
          if (j < flags.length - 1 && !flags[j + 1].match(/[a-z]/i)) {
            // Next character is not a letter (value follows)
            args[alias] = flags.slice(j + 1).join('');
            break;
          } else if (i + 1 < argv.length && !argv[i + 1].startsWith('-')) {
            // Value in next argument
            args[alias] = argv[++i];
            break;
          } else {
            // Boolean flag
            args[alias] = true;
          }
        } else {
          args[flag] = true;
        }
      }
    }
    else {
      args._.push(arg);
    }
  }

  // Apply defaults
  args.count = parseInt(args.count) || 10;
  args.offset = parseInt(args.offset) || 0;
  args.timeout = parseInt(args.timeout) || DEFAULT_TIMEOUT;
  args.safeSearch = args.safeSearch || 'moderate';

  return args;
}

/**
 * Summon HTML from the digital ether with enhanced Termux compatibility
 */
function fetchHtml(urlStr, options = {}) {
  return new Promise((resolve, reject) => {
    try {
      const url = new URL(urlStr);
      const client = url.protocol === 'https:' ? https : http;

      const reqOptions = {
        hostname: url.hostname,
        path: url.pathname + url.search + (url.hash || ''),
        method: 'GET',
        headers: {
          'User-Agent': USER_AGENT,
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.9',
          'Accept-Encoding': 'identity',
          'Connection': 'close',
          ...options.headers
        },
        timeout: options.timeout || DEFAULT_TIMEOUT,
        family: 4 // Prefer IPv4 for Termux compatibility
      };

      const req = client.request(reqOptions, (res) => {
        // Handle redirects
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          const redirectUrl = new URL(res.headers.location, url).toString();
          console.log(COLOR.YELLOW + `// Following redirect to ${redirectUrl.substring(0, 60)}...` + COLOR.RESET);
          fetchHtml(redirectUrl, options).then(resolve).catch(reject);
          return;
        }

        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode}: ${res.statusMessage || 'Unknown error'}`));
          return;
        }

        let data = '';
        res.setEncoding('utf8');
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => resolve(data));
      });

      req.on('error', (err) => {
        reject(new Error(`Request failed: ${err.message}`));
      });

      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Request timeout'));
      });

      req.end();
    } catch (err) {
      reject(new Error(`URL parsing failed: ${err.message}`));
    }
  });
}

/**
 * Enhanced Brave Search result parser with multiple fallback strategies
 */
function parseBraveSearchResults(html, maxResults = 10) {
  const results = [];
  const seenUrls = new Set();

  // Strategy 1: JSON-LD structured data (modern Brave)
  try {
    const jsonLdMatch = html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/i);
    if (jsonLdMatch) {
      const jsonData = JSON.parse(jsonLdMatch[1]);
      if (jsonData && jsonData.itemListElement) {
        for (const item of jsonData.itemListElement) {
          if (results.length >= maxResults) break;
          if (item.url && !seenUrls.has(item.url)) {
            seenUrls.add(item.url);
            results.push({
              title: item.name || item.headline || 'Untitled',
              url: item.url,
              description: item.description || 'No description available',
              position: results.length + 1
            });
          }
        }
        if (results.length > 0) return results;
      }
    }
  } catch (e) {
    console.log(COLOR.DIM + '// JSON-LD parsing failed, falling back to regex' + COLOR.RESET);
  }

  // Strategy 2: Regex pattern matching for Brave search results
  const resultPatterns = [
    // Modern Brave pattern
    {
      container: /<div[^>]+class="[^"]*snippet[^"]*"[^>]*>([\s\S]*?)<\/div>/gi,
      title: /<a[^>]+class="[^"]*result-title[^"]*"[^>]*href="([^"]+)"[^>]*>([^<]+)<\/a>/i,
      url: /href="([^"]+)"/i,
      desc: /<div[^>]+class="[^"]*snippet-description[^"]*"[^>]*>([\s\S]*?)<\/div>/i
    },
    // Fallback pattern
    {
      container: /<div[^>]+class="[^"]*result[^"]*"[^>]*>([\s\S]*?)<\/div>/gi,
      title: /<a[^>]+href="([^"]+)"[^>]*>([^<]+)<\/a>/i,
      url: /href="([^"]+)"/i,
      desc: /<p[^>]*>([\s\S]*?)<\/p>/i
    }
  ];

  for (const pattern of resultPatterns) {
    try {
      let containerMatch;
      while ((containerMatch = pattern.container.exec(html)) !== null && results.length < maxResults) {
        const containerHtml = containerMatch[1];

        const titleMatch = pattern.title.exec(containerHtml);
        const descMatch = pattern.desc.exec(containerHtml);
        const urlMatch = pattern.url.exec(containerHtml);

        let url = urlMatch ? urlMatch[1] : null;
        let title = titleMatch ? (titleMatch[2] || titleMatch[1]) : null;
        let description = descMatch ? descMatch[1] : null;

        // Clean extracted data
        if (url) url = url.replace(/&amp;/g, '&');
        if (title) title = title.replace(/<[^>]+>/g, '').trim();
        if (description) description = description.replace(/<[^>]+>/g, '').trim();

        // Validate and add result
        if (url && title && !seenUrls.has(url)) {
          seenUrls.add(url);
          results.push({
            title: title || 'Untitled',
            url: url,
            description: description || 'No description available',
            position: results.length + 1
          });
        }
      }
      if (results.length > 0) return results;
    } catch (e) {
      console.log(COLOR.DIM + '// Pattern matching failed, trying next strategy' + COLOR.RESET);
    }
  }

  // Strategy 3: Generic link extraction (fallback)
  try {
    const linkPattern = /<a[^>]+href="(https?:\/\/[^"]+)"[^>]*>([^<]{5,200})<\/a>/gi;
    let match;
    while ((match = linkPattern.exec(html)) !== null && results.length < maxResults) {
      const url = match[1];
      const title = match[2].replace(/<[^>]+>/g, '').trim();

      if (url && title && !seenUrls.has(url)) {
        seenUrls.add(url);
        results.push({
          title: title,
          url: url,
          description: 'Extracted from page content',
          position: results.length + 1
        });
      }
    }
    if (results.length > 0) return results;
  } catch (e) {
    console.log(COLOR.DIM + '// Generic extraction failed' + COLOR.RESET);
  }

  return results;
}

/**
 * Download content with enhanced Termux support
 */
async function downloadContent(url, index, options = {}) {
  const downloadId = `${Date.now()}-${index}`;
  const maxRetries = 3;
  let lastError = null;

  for (let attempt = 1; attempt <= maxRetries; attempt++) {
    try {
      const urlObj = new URL(url);
      const hostname = urlObj.hostname.replace(/[^a-zA-Z0-9]/g, '_');
      const timestamp = Date.now();
      const filename = `${index}_${hostname}_${timestamp}.html`;
      const filepath = path.join(DOWNLOAD_DIR, filename);
      const tempPath = path.join(DOWNLOAD_DIR, `.tmp_${filename}`);

      console.log(COLOR.CYAN + `// [${downloadId}] Summoning: ${url.substring(0, 50)} (Attempt ${attempt})` + COLOR.RESET);

      // Create temp file
      const content = await fetchHtml(url, {
        timeout: options.timeout || DEFAULT_TIMEOUT
      });

      // Ensure download directory exists
      if (!fs.existsSync(DOWNLOAD_DIR)) {
        fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
      }

      // Save to temp file first
      fs.writeFileSync(tempPath, content);

      // Extract text content for preview
      const textContent = content
        .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
        .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
        .replace(/<[^>]+>/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .substring(0, 500);

      // Move temp file to final destination
      fs.renameSync(tempPath, filepath);

      console.log(COLOR.GREEN + `// [${downloadId}] Saved: ${filename}` + COLOR.RESET);

      return {
        downloaded: true,
        filename: filename,
        filepath: filepath,
        size: content.length,
        preview: textContent + '...',
        attempt: attempt,
        timestamp: new Date().toISOString()
      };

    } catch (err) {
      lastError = err;
      console.error(COLOR.RED + `// [${downloadId}] Attempt ${attempt} failed: ${err.message}` + COLOR.RESET);

      // Clean up temp file if it exists
      try {
        const tempFiles = fs.readdirSync(DOWNLOAD_DIR).filter(f => f.startsWith(`.tmp_${index}_`));
        for (const tempFile of tempFiles) {
          fs.unlinkSync(path.join(DOWNLOAD_DIR, tempFile));
        }
      } catch (cleanupErr) {
        console.log(COLOR.DIM + `// [${downloadId}] Temp file cleanup failed` + COLOR.RESET);
      }

      // Exponential backoff
      const delay = Math.pow(2, attempt) * 1000;
      if (attempt < maxRetries) {
        console.log(COLOR.YELLOW + `// [${downloadId}] Retrying in ${delay}ms...` + COLOR.RESET);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
  }

  console.error(COLOR.RED + `// [${downloadId}] Failed after ${maxRetries} attempts` + COLOR.RESET);
  return {
    downloaded: false,
    error: lastError.message,
    timestamp: new Date().toISOString()
  };
}

/**
 * Parallel download manager with Termux resource awareness
 */
async function downloadResults(results, options = {}) {
  const downloadQueue = results.map((result, index) => ({
    url: result.url,
    index: index + 1
  }));

  const activeDownloads = new Set();
  const completedDownloads = [];

  // Limit concurrent downloads for Termux
  const concurrencyLimit = Math.min(MAX_CONCURRENT_DOWNLOADS, downloadQueue.length);

  async function processQueue() {
    while (downloadQueue.length > 0) {
      if (activeDownloads.size >= concurrencyLimit) {
        await new Promise(resolve => setTimeout(resolve, 100));
        continue;
      }

      const item = downloadQueue.shift();
      const downloadPromise = downloadContent(item.url, item.index, options)
        .then(result => {
          completedDownloads.push({
            ...result,
            originalUrl: item.url,
            originalIndex: item.index
          });
          activeDownloads.delete(downloadPromise);
        })
        .catch(err => {
          console.error(COLOR.RED + `// Download failed for ${item.url}: ${err.message}` + COLOR.RESET);
          completedDownloads.push({
            downloaded: false,
            error: err.message,
            originalUrl: item.url,
            originalIndex: item.index
          });
          activeDownloads.delete(downloadPromise);
        });

      activeDownloads.add(downloadPromise);
    }

    // Wait for all active downloads to complete
    while (activeDownloads.size > 0) {
      await Promise.race(Array.from(activeDownloads));
    }
  }

  await processQueue();

  // Sort completed downloads by original index
  completedDownloads.sort((a, b) => a.originalIndex - b.originalIndex);

  return completedDownloads;
}

// ════════════════════════════════════════════════════════════════════════════════
// Main Ritual - Enhanced Brave Search Scraper
// ════════════════════════════════════════════════════════════════════════════════
async function main() {
  console.log(COLOR.MAGENTA + '// Channeling Enhanced Brave Search Arcana...' + COLOR.RESET);

  // Parse command line arguments
  const argv = parseArgs(process.argv);

  // Validate required parameters
  if (!argv.query) {
    jsonError('Missing required argument: --query or -q', 400);
  }

  // Apply parameter constraints
  argv.count = Math.min(Math.max(parseInt(argv.count) || 10, 1), 50);
  argv.offset = Math.max(parseInt(argv.offset) || 0, 0);
  argv.timeout = Math.max(parseInt(argv.timeout) || DEFAULT_TIMEOUT, 1000);

  // Prepare output directories
  try {
    if (!fs.existsSync(CACHE_DIR)) {
      fs.mkdirSync(CACHE_DIR, { recursive: true });
    }
    if (argv.download && !fs.existsSync(DOWNLOAD_DIR)) {
      fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
    }
  } catch (err) {
    console.error(COLOR.RED + `// Directory creation failed: ${err.message}` + COLOR.RESET);
    // Continue anyway - we'll handle errors during actual operations
  }

  try {
    // Construct search URL (using Brave's search domain)
    const searchParams = new URLSearchParams();
    searchParams.append('q', argv.query);
    searchParams.append('count', argv.count);
    searchParams.append('offset', argv.offset);
    searchParams.append('safesearch', argv.safeSearch || 'moderate');

    // Language and country parameters
    if (argv.language) searchParams.append('hl', argv.language);
    if (argv.country) searchParams.append('gl', argv.country);

    const searchUrl = `https://search.brave.com/search?${searchParams.toString()}`;

    console.log(COLOR.BLUE + `// Summoning from: ${searchUrl.substring(0, 80)}...` + COLOR.RESET);

    // Fetch search results
    const html = await fetchHtml(searchUrl, {
      timeout: argv.timeout
    });

    // Parse results with enhanced parser
    let results = parseBraveSearchResults(html, argv.count + argv.offset);

    // Apply offset and limit
    results = results.slice(argv.offset, argv.offset + argv.count);

    if (results.length === 0) {
      console.log(COLOR.YELLOW + '// No results found, trying alternative parsing...' + COLOR.RESET);
      // Try alternative parsing with different patterns
      results = parseBraveSearchResults(html.replace(/<!--[\s\S]*?-->/g, ''), argv.count + argv.offset);
      results = results.slice(argv.offset, argv.offset + argv.count);
    }

    if (results.length === 0) {
      jsonError('No search results found in the digital ether', 404);
    }

    // Download content if requested
    if (argv.download) {
      console.log(COLOR.MAGENTA + `// Initiating download ritual for ${results.length} essences...` + COLOR.RESET);
      const downloadResults = await downloadResults(results, {
        timeout: argv.timeout
      });

      // Merge download results with search results
      for (let i = 0; i < results.length; i++) {
        results[i].download = downloadResults[i];
      }
    }

    // Construct metadata
    const metadata = {
      query: argv.query,
      totalResults: results.length,
      returnedResults: results.length,
      offset: argv.offset,
      count: argv.count,
      searchTime: Date.now(),
      language: argv.language || 'en',
      country: argv.country || 'us',
      safeSearch: argv.safeSearch || 'moderate',
      source: 'brave-search-scraper',
      userAgent: USER_AGENT,
      timestamp: new Date().toISOString()
    };

    // Build final output
    const output = {
      success: true,
      metadata: metadata,
      results: results
    };

    // Include raw HTML if requested
    if (argv.includeRaw) {
      output.raw_html = html.substring(0, 100000); // Limit to 100KB
    }

    // Output results
    jsonOut(output);

    console.log(COLOR.GREEN + `// Ritual complete: ${results.length} essences unveiled` + COLOR.RESET);
    if (argv.download) {
      const successfulDownloads = results.filter(r => r.download && r.download.downloaded).length;
      console.log(COLOR.CYAN + `// Downloads: ${successfulDownloads}/${results.length} successful` + COLOR.RESET);
    }

  } catch (error) {
    console.error(COLOR.RED + `// Ether disturbance: ${error.message}` + COLOR.RESET);

    // Enhanced error handling
    if (error.message.includes('timeout')) {
      jsonError('Search ritual timed out - the digital ether is congested', 504);
    } else if (error.message.includes('HTTP')) {
      jsonError(`Search gateway error: ${error.message}`, 502);
    } else {
      jsonError(`Arcane disturbance: ${error.message}`, 500);
    }
  }
}

// ════════════════════════════════════════════════════════════════════════════════
// Termux Integration - Enhanced Entry Point
// ════════════════════════════════════════════════════════════════════════════════
function termuxEnhancedEntry() {
  // Check if we're running in Termux
  const isTermux = process.env.PREFIX && process.env.PREFIX.includes('com.termux');

  if (isTermux) {
    console.log(COLOR.MAGENTA + '// Termux environment detected - activating mobile arcana...' + COLOR.RESET);

    // Check for Termux:API availability
    try {
      execSync('command -v termux-toast', { stdio: 'ignore' });
      console.log(COLOR.DIM + '// Termux:API available - notifications enabled' + COLOR.RESET);
    } catch (e) {
      console.log(COLOR.YELLOW + '// Termux:API not found - install with: pkg install termux-api' + COLOR.RESET);
    }

    // Check storage permissions
    try {
      const testFile = path.join(DOWNLOAD_DIR, '.termux_test');
      fs.writeFileSync(testFile, 'test');
      fs.unlinkSync(testFile);
    } catch (e) {
      console.log(COLOR.YELLOW + '// Storage permission may be required for downloads' + COLOR.RESET);
      console.log(COLOR.DIM + '// Grant permission with: termux-setup-storage' + COLOR.RESET);
    }
  }

  // Invoke main ritual
  main().catch(err => {
    console.error(COLOR.RED + '// Catastrophic ether disturbance:' + COLOR.RESET, err);
    process.exit(1);
  });
}

// Invoke the enhanced ritual
termuxEnhancedEntry();
