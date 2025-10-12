#!/usr/bin/env node

/**
 * Changelog generation script for automated releases
 * Parses conventional commits and generates structured changelog
 * 
 * Usage: node generate-changelog.js [--from=<ref>] [--to=<ref>] [--output=<file>]
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// Configuration
// Sanitize git references to prevent command injection
function sanitizeGitRef(ref) {
  if (!ref) return null;
  // Allow only safe characters for git refs
  if (!/^[a-zA-Z0-9._\/-]+$/.test(ref)) {
    throw new Error(`Invalid git reference: ${ref}`);
  }
  // Prevent directory traversal
  if (ref.includes('..')) {
    throw new Error(`Invalid git reference (directory traversal): ${ref}`);
  }
  return ref;
}

const COMMIT_TYPES = {
  feat: { title: '✨ Features', priority: 1 },
  fix: { title: '🐛 Bug Fixes', priority: 2 },
  perf: { title: '⚡ Performance Improvements', priority: 3 },
  refactor: { title: '♻️ Code Refactoring', priority: 4 },
  docs: { title: '📝 Documentation', priority: 5 },
  style: { title: '🎨 Styles', priority: 6 },
  test: { title: '✅ Tests', priority: 7 },
  build: { title: '📦 Build System', priority: 8 },
  ci: { title: '🔧 CI/CD', priority: 9 },
  chore: { title: '🔨 Chores', priority: 10 },
  revert: { title: '⏪ Reverts', priority: 11 }
};

// Parse command line arguments
function parseArgs() {
  const args = process.argv.slice(2);
  const options = {
    from: null,
    to: 'HEAD',
    output: null,
    format: 'markdown'
  };

  args.forEach(arg => {
    if (arg.startsWith('--from=')) {
      options.from = sanitizeGitRef(arg.split('=')[1]);
    } else if (arg.startsWith('--to=')) {
      options.to = sanitizeGitRef(arg.split('=')[1]);
    } else if (arg.startsWith('--output=')) {
      options.output = arg.split('=')[1];
    } else if (arg.startsWith('--format=')) {
      options.format = arg.split('=')[1];
    }
  });

  // If no 'from' specified, get the last tag
  if (!options.from) {
    try {
      options.from = execSync('git describe --tags --abbrev=0', { 
        encoding: 'utf-8',
        stdio: ['pipe', 'pipe', 'pipe'] // Suppress stderr
      }).trim();
      options.from = sanitizeGitRef(options.from);
    } catch (e) {
      console.log('No previous tag found, using first commit');
      try {
        options.from = execSync('git rev-list --max-parents=0 HEAD', { 
          encoding: 'utf-8' 
        }).trim();
        options.from = sanitizeGitRef(options.from);
      } catch (e2) {
        console.error('Failed to get initial commit:', e2.message);
        options.from = null;
      }
    }
  }

  return options;
}

// Get commits between two refs
function getCommits(from, to) {
  const range = from ? `${from}..${to}` : to;
  // Use unique separator to handle special characters in commit messages
  const fieldSeparator = '<--GIT-FIELD-->';
  const format = ['%H', '%s', '%b', '%an', '%ae', '%ad'].join(fieldSeparator);
  
  try {
    // Use null byte as commit separator for reliable parsing
    const output = execSync(`git log ${range} --format="${format}%x00" --no-merges`, { 
      encoding: 'utf-8',
      maxBuffer: 10 * 1024 * 1024 // 10MB buffer for large histories
    });
    
    return output.trim().split('\x00').filter(commit => commit).map(commit => {
      const [hash, subject, body, author, email, date] = commit.split(fieldSeparator);
      return { 
        hash: hash || '', 
        subject: subject || '', 
        body: body || '', 
        author: author || '', 
        email: email || '', 
        date: date || '' 
      };
    });
  } catch (e) {
    console.error('Error fetching commits:', e.message);
    process.exit(1); // Fail fast on critical errors
  }
}

// Parse conventional commit message
function parseCommit(commit) {
  const conventionalRegex = /^(\w+)(?:\(([^)]+)\))?\s*:\s*(.+)$/;
  const match = commit.subject.match(conventionalRegex);
  
  if (match) {
    const [, type, scope, description] = match;
    return {
      ...commit,
      type: type.toLowerCase(),
      scope,
      description,
      breaking: commit.subject.includes('!') || commit.body.includes('BREAKING CHANGE')
    };
  }
  
  // Not a conventional commit
  return {
    ...commit,
    type: 'other',
    description: commit.subject,
    breaking: false
  };
}

// Group commits by type
function groupCommits(commits) {
  const grouped = {};
  const breaking = [];
  
  commits.forEach(commit => {
    const parsed = parseCommit(commit);
    
    if (parsed.breaking) {
      breaking.push(parsed);
    }
    
    if (!grouped[parsed.type]) {
      grouped[parsed.type] = [];
    }
    grouped[parsed.type].push(parsed);
  });
  
  return { grouped, breaking };
}

// Generate markdown changelog
function generateMarkdown(commits, options) {
  const { grouped, breaking } = groupCommits(commits);
  const sections = [];
  
  // Header
  const version = process.env.VERSION || execSync('git describe --tags --always', { encoding: 'utf-8' }).trim();
  const date = new Date().toISOString().split('T')[0];
  
  sections.push(`# Changelog`);
  sections.push('');
  sections.push(`## ${version} (${date})`);
  sections.push('');
  
  // Breaking changes section
  if (breaking.length > 0) {
    sections.push('### ⚠️ BREAKING CHANGES');
    sections.push('');
    breaking.forEach(commit => {
      const scope = commit.scope ? `**${commit.scope}**: ` : '';
      sections.push(`- ${scope}${commit.description} ([${commit.hash.substring(0, 7)}](../../commit/${commit.hash}))`);
      if (commit.body && commit.body.includes('BREAKING CHANGE')) {
        const breakingMsg = commit.body.split('BREAKING CHANGE:')[1].split('\n')[0].trim();
        if (breakingMsg) {
          sections.push(`  - ${breakingMsg}`);
        }
      }
    });
    sections.push('');
  }
  
  // Other sections
  const sortedTypes = Object.keys(grouped).sort((a, b) => {
    const priorityA = COMMIT_TYPES[a]?.priority || 99;
    const priorityB = COMMIT_TYPES[b]?.priority || 99;
    return priorityA - priorityB;
  });
  
  sortedTypes.forEach(type => {
    if (type === 'other' && grouped[type].length === 0) return;
    
    const typeInfo = COMMIT_TYPES[type] || { title: '📌 Other Changes', priority: 99 };
    sections.push(`### ${typeInfo.title}`);
    sections.push('');
    
    grouped[type].forEach(commit => {
      const scope = commit.scope ? `**${commit.scope}**: ` : '';
      const author = commit.author !== 'github-actions[bot]' ? ` (@${commit.author})` : '';
      sections.push(`- ${scope}${commit.description} ([${commit.hash.substring(0, 7)}](../../commit/${commit.hash}))${author}`);
    });
    sections.push('');
  });
  
  // Stats section
  const stats = getRepoStats(options.from, options.to);
  sections.push('### 📊 Statistics');
  sections.push('');
  sections.push(`- **Commits**: ${commits.length}`);
  sections.push(`- **Files Changed**: ${stats.filesChanged}`);
  sections.push(`- **Additions**: ${stats.additions}`);
  sections.push(`- **Deletions**: ${stats.deletions}`);
  sections.push(`- **Contributors**: ${stats.contributors}`);
  sections.push('');
  
  // Footer
  if (options.from && options.to) {
    const compareUrl = getCompareUrl(options.from, options.to);
    sections.push(`**Full Changelog**: ${compareUrl}`);
    sections.push('');
  }
  
  return sections.join('\n');
}

// Get repository statistics
function getRepoStats(from, to) {
  const range = from ? `${from}..${to}` : to;
  
  try {
    // Get file stats
    const diffStat = execSync(`git diff ${range} --shortstat`, { encoding: 'utf-8' }).trim();
    const match = diffStat.match(/(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?/);
    
    // Get unique contributors (using execSync with shell: true for pipeline)
    const contributors = execSync(`git log ${range} --format="%an" | sort -u | wc -l`, { 
      encoding: 'utf-8',
      shell: true
    }).trim();
    
    return {
      filesChanged: match ? parseInt(match[1], 10) : 0,
      additions: match && match[2] ? parseInt(match[2], 10) : 0,
      deletions: match && match[3] ? parseInt(match[3], 10) : 0,
      contributors: parseInt(contributors, 10)
    };
  } catch (e) {
    return {
      filesChanged: 0,
      additions: 0,
      deletions: 0,
      contributors: 0
    };
  }
}

// Get GitHub compare URL
function getCompareUrl(from, to) {
  try {
    const remoteUrl = execSync('git config --get remote.origin.url', { encoding: 'utf-8' }).trim();
    const match = remoteUrl.match(/github\.com[:/](.+?)(?:\.git)?$/);
    
    if (match) {
      const repo = match[1];
      return `https://github.com/${repo}/compare/${from}...${to}`;
    }
  } catch (e) {
    // Fallback
  }
  
  return `[${from}...${to}]`;
}

// Generate JSON changelog
function generateJSON(commits, options) {
  const { grouped, breaking } = groupCommits(commits);
  const version = process.env.VERSION || execSync('git describe --tags --always', { encoding: 'utf-8' }).trim();
  
  return JSON.stringify({
    version,
    date: new Date().toISOString(),
    from: options.from,
    to: options.to,
    breaking,
    changes: grouped,
    stats: getRepoStats(options.from, options.to),
    commits: commits.map(c => ({
      hash: c.hash,
      subject: c.subject,
      author: c.author,
      date: c.date
    }))
  }, null, 2);
}

// Main function
function main() {
  const options = parseArgs();
  console.log(`Generating changelog from ${options.from} to ${options.to}...`);
  
  const commits = getCommits(options.from, options.to);
  
  if (commits.length === 0) {
    console.log('No commits found in the specified range');
    return;
  }
  
  console.log(`Found ${commits.length} commits`);
  
  let changelog;
  if (options.format === 'json') {
    changelog = generateJSON(commits, options);
  } else {
    changelog = generateMarkdown(commits, options);
  }
  
  if (options.output) {
    fs.writeFileSync(options.output, changelog, 'utf-8');
    console.log(`Changelog written to ${options.output}`);
  } else {
    console.log('\n' + changelog);
  }
}

// Run if called directly
if (require.main === module) {
  main();
}

module.exports = {
  parseCommit,
  groupCommits,
  generateMarkdown,
  generateJSON,
  getCommits
};