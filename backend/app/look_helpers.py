from sqlmodel import Session, select
from .engine import engine
from .models import Effect, EffectLayer, Light


def layer_targets(row, effect, all_ids):
    ids = set(row.mask_light_ids or row.light_ids or (effect.light_ids if effect else []) or [])
    ids.update(t['light_id'] for t in (row.targets or []) if 'light_id' in t)
    return ids or set(all_ids)


def saved_layers_for(sess: Session, affected: set[int]) -> list[dict]:
    active = {x.get('layer_id') for x in engine.layer_snapshot()}
    all_ids = set(sess.exec(select(Light.id)).all())
    out = []
    for row in sess.exec(select(EffectLayer).order_by(EffectLayer.z_index)).all():
        if row.id not in active:
            continue
        effect = sess.get(Effect, row.effect_id) if row.effect_id else None
        targets = layer_targets(row, effect, all_ids) & affected
        if targets:
            saved = row.model_dump(mode='json')
            for key in ('id', 'created_at', 'last_error', 'error_count', 'last_tick_ms'):
                saved.pop(key, None)
            saved['mask_light_ids'] = sorted(targets)
            out.append(saved)
    return out


def stop_layers_for(sess: Session, affected: set[int]) -> None:
    all_ids = set(sess.exec(select(Light.id)).all())
    for row in sess.exec(select(EffectLayer)).all():
        effect = sess.get(Effect, row.effect_id) if row.effect_id else None
        if layer_targets(row, effect, all_ids) & affected:
            engine.stop_by_layer_id(row.id, immediate=True)
            sess.delete(row)
            if effect:
                effect.is_active = False
                sess.add(effect)
    # Include transient effects that have no persistent row.
    engine.stop_affecting(affected, immediate=True)
    sess.flush()
