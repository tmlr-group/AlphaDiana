import type * as Preset from '@docusaurus/preset-classic';
import type { Config } from '@docusaurus/types';
import { themes as prismThemes } from 'prism-react-renderer';

const config: Config = {
  title: 'AlphaDiana',
  tagline: 'A System for Evaluating Reasoning Agents',
  favicon: 'img/TMLR.ico',

  future: {
    v4: true,
  },

  // Canonical production origin used for metadata and generated absolute URLs.
  url: 'https://alphadiana.org',
  baseUrl: '/',

  organizationName: 'tmlr-group',
  projectName: 'AlphaDiana',

  onBrokenLinks: 'throw',
  onBrokenAnchors: 'throw',

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
          // Edit links stay disabled until these docs are published in a
          // publicly editable source repository.
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
        { to: '/docs/concepts/harness-aware-evaluation', label: 'Results', position: 'left' },
        { to: '/docs/dashboard', label: 'Dashboard', position: 'left' },
        { href: 'https://github.com/tmlr-group/AlphaDiana', label: 'GitHub', position: 'right' },
      ],
    },
    footer: {
      style: 'dark',
      copyright: `Copyright © ${new Date().getFullYear()} AlphaDiana. Built with Docusaurus. All rights reserved. For technical issues, please submit an issue on <a href="https://github.com/tmlr-group/AlphaDiana/issues" target="_blank" rel="noopener noreferrer">GitHub</a>.<br />For questions and discussions, please contact Prof. Bo Han (<a href="mailto:bhanml@comp.hkbu.edu.hk">bhanml@comp.hkbu.edu.hk</a>) or Zhanke Zhou (<a href="mailto:cszkzhou@comp.hkbu.edu.hk">cszkzhou@comp.hkbu.edu.hk</a>).`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['python', 'bash', 'yaml', 'toml', 'json', 'diff'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
