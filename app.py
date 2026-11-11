import json
import math
import os
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder='.')

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, 'materials.json')) as f:
  materials = json.load(f)


@app.after_request
def allow_cors(resp):
  resp.headers['Access-Control-Allow-Origin'] = '*'
  resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
  return resp


@app.route('/')
def index():
  return render_template('index.html')


@app.route('/materials')
def get_materials():
  return jsonify([m['name'] for m in materials])


def principal_stresses(sx, sy, txy):
  avg = (sx + sy) / 2
  r = math.sqrt(((sx - sy) / 2) ** 2 + txy ** 2)
  s = sorted([avg + r, avg - r, 0], reverse=True)
  return s[0], s[1], s[2]


def get_limits(mat):
  # Infer material type based on category or explicit property
  mat_type = mat.get('type', 'brittle' if mat.get('category') in ['Cast Irons', 'Civil Materials'] else 'ductile')
  sy = mat.get('yield_strength_MPa', 250)
  sut = mat.get('ultimate_strength_MPa', sy)
  
  if mat_type == 'ductile':
    return sy, sy
  # For brittle materials, use ultimate tensile and compressive limits (defaulting compressive to 4x tensile if not specified)
  suc = mat.get('compressive_strength_MPa', sut * 4)
  return sut, suc


def fos_from_value(value, st, sc):
  if value > 0:
    return st / value
  if value < 0:
    return sc / abs(value)
  return None


def max_normal(s1, s2, s3, st, sc, nu):
  vals = []
  for s in (s1, s3):
    f = fos_from_value(s, st, sc)
    if f is not None:
      vals.append(f)
  return min(vals) if vals else None


def max_strain(s1, s2, s3, st, sc, nu):
  vals = []
  for e in (s1 - nu * (s2 + s3), s3 - nu * (s1 + s2)):
    f = fos_from_value(e, st, sc)
    if f is not None:
      vals.append(f)
  return min(vals) if vals else None


def max_shear(s1, s2, s3, st, sc, nu):
  d = s1 - s3
  return st / d if d != 0 else None


def strain_energy(s1, s2, s3, st, sc, nu):
  v = (
      s1 ** 2
      + s2 ** 2
      + s3 ** 2
      - 2 * nu * (s1 * s2 + s2 * s3 + s1 * s3)
  )
  return st / math.sqrt(v) if v > 0 else None


def von_mises(s1, s2, s3, st, sc, nu):
  v = s1 ** 2 + s2 ** 2 + s3 ** 2 - s1 * s2 - s2 * s3 - s1 * s3
  return st / math.sqrt(v) if v > 0 else None


theories = [
    ('Maximum Principal Stress (Rankine)', max_normal),
    ('Maximum Principal Strain (St. Venant)', max_strain),
    ('Maximum Shear Stress (Tresca)', max_shear),
    ('Maximum Strain Energy (Beltrami-Haigh)', strain_energy),
    ('Distortion Energy (von Mises)', von_mises),
]


@app.route('/calculate', methods=['POST'])
def calculate():
  data = request.get_json()
  mat = next((m for m in materials if m['name'] == data.get('material')), None)
  if mat is None:
    return jsonify({'error': 'Material not found'}), 400
  try:
    sx = float(data['sx'])
    sy = float(data['sy'])
    txy = float(data['txy'])
  except (KeyError, ValueError, TypeError):
    return jsonify({'error': 'Enter valid numbers'}), 400

  s1, s2, s3 = principal_stresses(sx, sy, txy)
  st, sc = get_limits(mat)
  nu = mat.get('poissons_ratio', 0.3)

  results = []
  for name, func in theories:
    f = func(s1, s2, s3, st, sc, nu)
    results.append({'theory': name, 'fos': None if f is None else round(f, 3)})

  valid = [r for r in results if r['fos'] is not None]
  if valid:
    gov = next(
        (r for r in valid if 'von Mises' in r['theory']),
        min(valid, key=lambda r: r['fos']),
    )
    governing = gov['theory']
    safe = gov['fos'] >= 1
  else:
    governing = 'No stress applied'
    safe = True

  return jsonify({
      'principal': [round(s1, 2), round(s2, 2), round(s3, 2)],
      'results': results,
      'governing': governing,
      'safe': safe,
  })


@app.route('/calculate_dimension', methods=['POST'])
def calculate_dimension():
  data = request.get_json()
  mat = next((m for m in materials if m['name'] == data.get('material')), None)
  if mat is None:
    return jsonify({'error': 'Material not found'}), 400

  try:
    M = float(data.get('M', 0)) * 1e3  # kN·m to N·m
    T = float(data.get('T', 0)) * 1e3  # kN·m to N·m
    fos = float(data.get('fos', 2.0))
    shape = data.get('shape', 'circle')
    aspect_ratio = float(data.get('aspect_ratio', 1.0))
  except (ValueError, TypeError):
    return jsonify({'error': 'Invalid numerical inputs'}), 400

  st, sc = get_limits(mat)
  nu = mat.get('poissons_ratio', 0.3)
  sig_allow = st / fos

  Me_tresca = 0.5 * (M + math.sqrt(M ** 2 + T ** 2))
  Te_tresca = math.sqrt(M ** 2 + T ** 2)
  Te_mises = math.sqrt(M ** 2 + 0.75 * (T ** 2))

  results = []

  if shape == 'circle':
    d_rankine = ((32 * Me_tresca) / (math.pi * sig_allow)) ** (1 / 3) * 1e3
    Me_stvenant = 0.5 * (
        (1 - nu) * M + (1 + nu) * math.sqrt(M ** 2 + T ** 2)
    )
    d_stvenant = ((32 * Me_stvenant) / (math.pi * sig_allow)) ** (1 / 3) * 1e3
    d_tresca = ((16 * Te_tresca) / (math.pi * (0.5 * sig_allow))) ** (
        1 / 3
    ) * 1e3
    d_mises = ((16 * Te_mises) / (math.pi * (0.5 * sig_allow))) ** (
        1 / 3
    ) * 1e3
    Me_strain_energy = math.sqrt(M ** 2 + 2 * (1 + nu) * (T ** 2) / 2)
    d_beltrami = ((32 * Me_strain_energy) / (math.pi * sig_allow)) ** (
        1 / 3
    ) * 1e3

    results = [
        {
            'theory': 'Maximum Principal Stress (Rankine)',
            'dim': round(d_rankine, 2),
            'unit': 'mm (Diameter)',
        },
        {
            'theory': 'Maximum Principal Strain (St. Venant)',
            'dim': round(d_stvenant, 2),
            'unit': 'mm (Diameter)',
        },
        {
            'theory': 'Maximum Shear Stress (Tresca)',
            'dim': round(d_tresca, 2),
            'unit': 'mm (Diameter)',
        },
        {
            'theory': 'Maximum Strain Energy (Beltrami-Haigh)',
            'dim': round(d_beltrami, 2),
            'unit': 'mm (Diameter)',
        },
        {
            'theory': 'Distortion Energy (von Mises)',
            'dim': round(d_mises, 2),
            'unit': 'mm (Diameter)',
        },
    ]
  elif shape == 'square':
    a_mises = ((6 * Te_mises) / sig_allow) ** (1 / 3) * 1e3
    a_tresca = ((6 * Te_tresca) / sig_allow) ** (1 / 3) * 1e3
    a_rankine = ((6 * Me_tresca) / sig_allow) ** (1 / 3) * 1e3
    results = [
        {
            'theory': 'Maximum Principal Stress (Rankine)',
            'dim': round(a_rankine, 2),
            'unit': 'mm (Side)',
        },
        {
            'theory': 'Maximum Shear Stress (Tresca)',
            'dim': round(a_tresca, 2),
            'unit': 'mm (Side)',
        },
        {
            'theory': 'Distortion Energy (von Mises)',
            'dim': round(a_mises, 2),
            'unit': 'mm (Side)',
        },
    ]
  elif shape == 'rectangle':
    k = aspect_ratio
    b_mises = ((6 * Te_mises) / (k ** 2 * sig_allow)) ** (1 / 3) * 1e3
    h_mises = k * b_mises
    results = [{
        'theory': 'Distortion Energy (von Mises)',
        'dim': f'b = {round(b_mises, 2)} mm, h = {round(h_mises, 2)}',
        'unit': 'mm (Width x Height)',
    }]

  gov = next((r for r in results if 'von Mises' in r['theory']), results[0])

  return jsonify({
      'shape': shape,
      'results': results,
      'governing': gov['theory'],
      'recommended_dim': f"{gov['dim']} {gov['unit']}",
  })


if __name__ == '__main__':
  app.run(debug=True)