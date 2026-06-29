import React, {ReactNode} from 'react';
import clsx from 'clsx';
import FeatureCard from './FeatureCard';
import styles from './styles.module.css';

export interface Feature {
  icon: ReactNode;
  title: string;
  description: string;
  link?: string;
}

export interface FeaturesGridProps {
  features: Feature[];
  title?: string;
  buttonText?: string;
  buttonLink?: string;
  background?: 'white' | 'gray';
}

export default function FeaturesGrid({
  features,
  title,
  buttonLink,
  buttonText,
  background = 'white',
}: FeaturesGridProps) {
  return (
    <section className={clsx(background === 'gray' ? 'section-gray' : 'section-white')}>
      <div className={styles.container}>
        {title ? <h2 className={styles.heading}>{title}</h2> : null}
        <div className={styles.grid}>
          {features.map((feature) => (
            <FeatureCard key={feature.title} {...feature} />
          ))}
        </div>
        {buttonLink && buttonText ? (
          <div className={styles.cta}>
            <a className="button button--primary button--lg" href={buttonLink}>
              {buttonText}
            </a>
          </div>
        ) : null}
      </div>
    </section>
  );
}
