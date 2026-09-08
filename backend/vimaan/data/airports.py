"""
City name to IATA code.

DGCA reports traffic by city name; fares arrive with IATA codes. Everything
downstream keys on a single route id, so the two have to be reconciled in one
place — here — rather than in each caller.

Only cities with scheduled domestic service are listed. An unmapped city is
dropped from the weighted panel rather than guessed at: a wrong mapping would
attach the wrong passenger weight to a route, which is worse than a gap.
"""
from __future__ import annotations

CITY_IATA: dict[str, str] = {
    "DELHI": "DEL", "MUMBAI": "BOM", "BENGALURU": "BLR", "BANGALORE": "BLR",
    "CHENNAI": "MAA", "KOLKATA": "CCU", "HYDERABAD": "HYD", "PUNE": "PNQ",
    "AHMEDABAD": "AMD", "GOA": "GOI", "GOA (MOPA)": "GOX", "KOCHI": "COK",
    "COCHIN": "COK", "JAIPUR": "JAI", "LUCKNOW": "LKO", "GUWAHATI": "GAU",
    "SRINAGAR": "SXR", "PATNA": "PAT", "BHUBANESWAR": "BBI", "INDORE": "IDR",
    "NAGPUR": "NAG", "CHANDIGARH": "IXC", "COIMBATORE": "CJB",
    "THIRUVANANTHAPURAM": "TRV", "TRIVANDRUM": "TRV", "VARANASI": "VNS",
    "RANCHI": "IXR", "AMRITSAR": "ATQ", "VISAKHAPATNAM": "VTZ",
    "RAIPUR": "RPR", "DEHRADUN": "DED", "UDAIPUR": "UDR", "MADURAI": "IXM",
    "TIRUPATI": "TIR", "MANGALURU": "IXE", "MANGALORE": "IXE",
    "VADODARA": "BDQ", "SURAT": "STV", "RAJKOT": "HSR", "JODHPUR": "JDH",
    "LEH": "IXL", "JAMMU": "IXJ", "IMPHAL": "IMF", "AGARTALA": "IXA",
    "DIBRUGARH": "DIB", "SILCHAR": "IXS", "AIZAWL": "AJL", "DIMAPUR": "DMU",
    "BAGDOGRA": "IXB", "PORT BLAIR": "IXZ", "BHOPAL": "BHO",
    "AURANGABAD": "IXU", "TIRUCHIRAPPALLI": "TRZ", "HUBLI": "HBX",
    "BELAGAVI": "IXG", "KANNUR": "CNN", "KOZHIKODE": "CCJ",
    "GORAKHPUR": "GOP", "PRAYAGRAJ": "IXD", "ALLAHABAD": "IXD",
    "JABALPUR": "JLR", "GWALIOR": "GWL", "AGRA": "AGR", "KANPUR": "KNU",
    "BHUJ": "BHJ", "JAMNAGAR": "JGA", "PORBANDAR": "PBD", "DIU": "DIU",
    "SHILLONG": "SHL", "ITANAGAR": "HGI", "JORHAT": "JRH", "TEZPUR": "TEZ",
    "LILABARI": "IXI", "PASIGHAT": "IXT", "SHIMLA": "SLV", "KULLU": "KUU",
    "DHARAMSHALA": "DHM", "PANTNAGAR": "PGH", "PITHORAGARH": "NNP",
    "HINDON AIRPORT": "HDO", "ADAMPUR": "AIP", "BATHINDA": "BUP",
    "LUDHIANA": "LUH", "PAKYONG": "PYG", "RUPSI": "RUP", "TEZU": "TEI",
    "ZIRO": "ZER", "SALEM": "SXV", "TUTICORIN": "TCR", "VIJAYAWADA": "VGA",
    "RAJAHMUNDRY": "RJA", "KADAPA": "CDP", "KURNOOL": "KJB",
    "PUDUCHERRY": "PNY", "MYSURU": "MYQ", "SHIRDI": "SAG",
    "KOLHAPUR": "KLH", "NASHIK": "ISK", "GONDIA": "GDB", "AKOLA": "AKD",
    "KISHANGARH": "KQH", "BIKANER": "BKB", "JAISALMER": "JSA",
    "DEOGHAR": "DGH", "DARBHANGA": "DBR", "GAYA": "GAY", "DURGAPUR": "RDP",
    "JHARSUGUDA": "JRG", "JEYPORE": "PYB", "ROURKELA": "RRK",
}


def iata(city: str) -> str | None:
    """IATA code for a DGCA city name, or None if we have no confident mapping."""
    return CITY_IATA.get((city or "").strip().upper())


def route_key(city_a: str, city_b: str) -> str | None:
    """Direction-independent IATA route id, or None if either city is unmapped."""
    a, b = iata(city_a), iata(city_b)
    if not a or not b or a == b:
        return None
    lo, hi = sorted((a, b))
    return f"{lo}-{hi}"
