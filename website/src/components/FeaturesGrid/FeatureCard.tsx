import React, {ReactNode} from 'react';
import styles from './styles.module.css';

interface FeatureCardProps {
  icon: ReactNode;
  title: string;
  description: string;
  link?: string;
}

export default function FeatureCard({icon, title, description, link}: FeatureCardProps) {
  const content = (
    <div className={styles.card}>
      <div className={styles.icon}>{icon}</div>
      <h3 className={styles.title}>{title}</h3>
      <p className={styles.description}>{description}</p>
    </div>
  );

  if (link) {
    return (
      <a className={styles.cardLink} href={link}>
        {content}
      </a>
    );
  }

  return content;
}
