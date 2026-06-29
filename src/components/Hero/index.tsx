import Link from '@docusaurus/Link';
import clsx from 'clsx';
import styles from './styles.module.css';

export interface HeroProps {
  title: string;
  description: string;
  primaryButtonText: string;
  primaryButtonLink: string;
  secondaryButtonText: string;
  secondaryButtonLink: string;
  paperButtonText?: string;
  paperButtonLink?: string;
  illustrationSrc?: string;
}

export default function Hero({
  title,
  description,
  primaryButtonLink,
  primaryButtonText,
  secondaryButtonLink,
  secondaryButtonText,
  paperButtonText,
  paperButtonLink,
  illustrationSrc,
}: HeroProps) {
  return (
    <section className={clsx(styles.hero, 'section-white')}>
      <div className={styles.inner}>
        <div className={styles.text}>
          <p className={styles.kicker}></p>
          <h1 className={styles.title}>{title}</h1>
          <p className={styles.description}>{description}</p>
          <div className={styles.actions}>
            <Link className="button button--primary button--lg" to={primaryButtonLink}>
              {primaryButtonText}
            </Link>
            <Link className="button button--secondary button--lg" to={secondaryButtonLink}>
              {secondaryButtonText}
            </Link>
            {paperButtonLink && paperButtonText ? (
              <Link className="button button--secondary button--lg" href={paperButtonLink}>
                {paperButtonText}
              </Link>
            ) : null}
          </div>
        </div>
        {illustrationSrc ? (
          <div className={styles.illustration}>
            <img src={illustrationSrc} alt="System architecture" />
          </div>
        ) : null}
      </div>
    </section>
  );
}
