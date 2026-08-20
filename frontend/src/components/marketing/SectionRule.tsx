import { motion, useReducedMotion } from "motion/react";
import { marketing as m } from "../../theme";

interface Props {
  /** Short structural label. Encodes what the section IS. */
  eyebrow?: string;
  /** Section heading. Comes from i18n — never hardcode copy here. */
  title?: string;
  /** Anchor target, e.g. "pricing" for the #pricing link. */
  id?: string;
}

/**
 * The page's structural grammar: a full-bleed rule with an optional
 * eyebrow and heading sitting on it. Replaces the centred
 * `text-3xl font-bold` heading that every section used to share.
 */
export default function SectionRule({ eyebrow, title, id }: Props) {
  const reduce = useReducedMotion();

  return (
    <div id={id} className="scroll-mt-20">
      <motion.div
        className={`border-t ${m.rule.heavy} origin-[left_center] rtl:origin-[right_center]`}
        initial={reduce ? false : { scaleX: 0 }}
        whileInView={{ scaleX: 1 }}
        viewport={{ once: true, margin: "-80px" }}
        transition={{ duration: 0.3, ease: "easeOut" }}
      />
      {(eyebrow || title) && (
        <div className="pt-6 pb-10 flex flex-col gap-3 md:flex-row md:items-baseline md:gap-8">
          {eyebrow && (
            <span className={`${m.text.meta} shrink-0 md:w-40`}>{eyebrow}</span>
          )}
          {title && (
            <h2
              className={`${m.text.display} font-display text-3xl md:text-5xl font-semibold leading-[1.05]`}
            >
              {title}
            </h2>
          )}
        </div>
      )}
    </div>
  );
}
