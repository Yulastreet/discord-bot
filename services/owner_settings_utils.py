def normalize_seasonal_sabre_name(name):
    value = str(name or "").strip()
    if not value:
        raise ValueError("missing_name")
    if len(value) > 80:
        raise ValueError("name_too_long")
    return value


def update_seasonal_sabre_name(get_sabre, update_sabre, sabre_id, name):
    sabre_id = str(sabre_id or "")
    if not sabre_id.startswith("season_"):
        raise ValueError("not_seasonal")
    if not get_sabre(sabre_id):
        raise LookupError("sabre_not_found")
    return update_sabre(sabre_id, {"nom": normalize_seasonal_sabre_name(name)})
