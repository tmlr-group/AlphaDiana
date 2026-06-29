import type * as Preset from '@docusaurus/preset-classic';
import type { Config } from '@docusaurus/types';
import { themes as prismThemes } from 'prism-react-renderer';

const config: Config = {
  title: 'AlphaDiana',
  tagline: 'Harness-Aware Evaluation of Open Agents on Verifiable Reasoning Tasks',
  favicon: 'img/TMLR.ico',

  future: {
    v4: true,
  },

  // TODO: set to the final hosting URL before deploying.
  url: 'https://alphadiana.org',
  baseUrl: '/',

  organizationName: 'tmlr-group',
  projectName: 'AlphaDiana',

  // Docs link to repo source files (e.g. ../../configs/examples/*.yaml) that are
  // not Docusaurus pages, so they resolve on GitHub but not in the built site.
  // Warn rather than fail the build, and leave the docs content untouched.
  onBrokenLinks: 'warn',
  // Navbar "Results"/"Dashboard" link to homepage anchors; the navbar renders on
  // every page, so per-page anchor checking false-positives. Ignore anchors only.
  onBrokenAnchors: 'ignore',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          lastVersion: 'current',
          versions: {
            current: {
              label: 'Latest',
              path: '',
            },
          },
          editUrl: 'https://github.com/tmlr-group/AlphaDiana',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themes: ['@docusaurus/theme-mermaid'],

  markdown: {
    // Parse docs as CommonMark (not MDX) so technical prose like "<= 3.12" and
    // "<https://...>" isn't interpreted as JSX. Keeps the docs build without
    // editing their content.
    format: 'md',
    mermaid: true,
  },

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'AlphaDiana',
      logo: {
        alt: 'AlphaDiana Logo',
        src: 'img/TMLR.png',
      },
      items: [
        { to: '/docs/intro', label: 'Docs', position: 'left' },
        { to: '/#results', label: 'Results', position: 'left' },
        { to: '/#dashboard', label: 'Dashboard', position: 'left' },
        { href: 'https://github.com/tmlr-group/AlphaDiana', label: 'GitHub', position: 'right' },
      ],
    },
    footer: {
      style: 'dark',
      copyright: `Copyright © ${new Date().getFullYear()} AlphaDiana. Built with Docusaurus. All rights reserved. For technical issues, please submit an issue on <a href="https://github.com/tmlr-group/AlphaDiana/issues" target="_blank" rel="noopener noreferrer">GitHub</a>. </br>For questions and discussions, please contact Prof. Bo Han (<a href="mailto:bhanml@comp.hkbu.edu.hk">bhanml@comp.hkbu.edu.hk</a>) or Zhanke Zhou (<a href="mailto:cszkzhou@comp.hkbu.edu.hk">cszkzhou@comp.hkbu.edu.hk</a>).`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['python', 'bash', 'yaml', 'toml', 'json', 'diff'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
