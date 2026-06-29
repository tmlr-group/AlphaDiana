import React, {useState} from 'react';
import clsx from 'clsx';
import styles from './styles.module.css';

export interface InstallBoxProps {
  command: string;
  language?: 'bash' | 'python';
  showCopyButton?: boolean;
  className?: string;
}

const copyToClipboard = async (text: string): Promise<boolean> => {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    console.error('Copy failed', err);
    return false;
  }
};

export default function InstallBox({
  command,
  language = 'bash',
  showCopyButton = true,
  className,
}: InstallBoxProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const ok = await copyToClipboard(command);
    setCopied(ok);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className={clsx(styles.box, className)}>
      <div className={styles.header}>
        <span className={styles.language}>{language}</span>
        {showCopyButton ? (
          <button
            type="button"
            aria-label="Copy command"
            className={styles.copyButton}
            onClick={handleCopy}>
            {copied ? 'Copied' : 'Copy'}
          </button>
        ) : null}
      </div>
      <pre className={styles.code}>
        <code>{command}</code>
      </pre>
    </div>
  );
}
