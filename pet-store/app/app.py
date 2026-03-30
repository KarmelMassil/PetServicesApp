from flask import Flask, request, jsonify, send_file
import requests
import os
import re
from datetime import datetime
from typing import Optional, List
from pymongo import MongoClient, ReturnDocument
import time

app = Flask(__name__)

pictures_dir = "pictures"
os.makedirs(pictures_dir, exist_ok=True)

NINJA_API_KEY    = os.environ.get('NINJA_API_KEY')
MONGO_URL        = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
STORE_COLLECTION = os.environ.get('STORE_COLLECTION', 'petstore1')


# ---------------------------------------------------------------------------
# MongoDB connection (with retry so the app waits for the DB to be ready)
# ---------------------------------------------------------------------------
def connect_to_mongo():
    for attempt in range(30):
        try:
            c = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
            c.admin.command('ping')
            print(f"[{STORE_COLLECTION}] Connected to MongoDB", flush=True)
            return c
        except Exception as e:
            print(f"[{STORE_COLLECTION}] MongoDB attempt {attempt + 1} failed: {e}", flush=True)
            time.sleep(3)
    raise RuntimeError("Could not connect to MongoDB after 30 attempts")


mongo_client = connect_to_mongo()
db           = mongo_client['petstoredb']
col          = db[STORE_COLLECTION]                          # pet-type documents
counters_col = db[f'{STORE_COLLECTION}_counters']            # ID counter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_next_id() -> str:
    result = counters_col.find_one_and_update(
        {'_id': 'pet_type_id'},
        {'$inc': {'value': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return str(result['value'])


def norm(value):
    """Lowercase a string, pass through other types unchanged."""
    return value.lower() if isinstance(value, str) else value


def format_output(doc: dict) -> dict:
    """Convert a MongoDB pet-type document to the API response format."""
    return {
        'id':         doc['_id'],
        'type':       doc['type'],
        'family':     doc['family'],
        'genus':      doc['genus'],
        'attributes': doc['attributes'],
        'lifespan':   doc['lifespan'],
        'pets':       [p['name'] for p in doc.get('pets', [])]
    }


# ---------------------------------------------------------------------------
# Ninja API helpers (unchanged from assignment 1)
# ---------------------------------------------------------------------------
def get_animal_info_from_ninja(animal_name: str) -> Optional[dict]:
    api_url = 'https://api.api-ninjas.com/v1/animals'
    headers = {'X-Api-Key': NINJA_API_KEY}
    params  = {'name': animal_name}
    try:
        response = requests.get(api_url, headers=headers, params=params)
        if response.status_code == 200:
            animals = response.json()
            if not animals:
                return None
            for animal in animals:
                if norm(animal.get('name', '')) == norm(animal_name):
                    return animal
            return None
        raise Exception(f"API response code {response.status_code}")
    except Exception as e:
        raise e


def parse_lifespan(lifespan_str: str) -> Optional[int]:
    if not lifespan_str:
        return None
    numbers = re.findall(r'\d+', lifespan_str)
    return int(min(numbers, key=int)) if numbers else None


def parse_attributes(characteristics: dict) -> List[str]:
    if 'temperament' in characteristics:
        text = characteristics['temperament']
    elif 'group_behavior' in characteristics:
        text = characteristics['group_behavior']
    else:
        return []
    return [w.lower() for w in re.findall(r'\b\w+\b', text)]


def download_image(url: str, pet_name: str, pet_type: str) -> str:
    try:
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/91.0.4472.124 Safari/537.36'
            ),
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
        }
        response = requests.get(url, timeout=30, headers=headers,
                                allow_redirects=True, stream=True)
        response.raise_for_status()

        content_type = response.headers.get('Content-Type', '').lower()
        if 'jpeg' in content_type or 'jpg' in content_type:
            ext = 'jpg'
        elif 'png' in content_type:
            ext = 'png'
        elif url.lower().endswith('.jpg') or url.lower().endswith('.jpeg'):
            ext = 'jpg'
        elif url.lower().endswith('.png'):
            ext = 'png'
        else:
            ext = 'jpg'

        content = response.content
        if len(content) == 0:
            raise Exception("Downloaded file is empty")

        filename = f"{pet_name}-{pet_type}.{ext}".replace(' ', '_')
        filepath = os.path.join(pictures_dir, filename)
        with open(filepath, 'wb') as f:
            f.write(content)
        return filename

    except requests.exceptions.Timeout:
        raise Exception("Image download timeout")
    except requests.exceptions.HTTPError as e:
        raise Exception(f"HTTP error: {e.response.status_code}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to download image: {str(e)}")
    except Exception as e:
        raise Exception(f"Image error: {str(e)}")


def validate_date(date_str: str) -> bool:
    if not re.match(r'^\d{2}-\d{2}-\d{4}$', date_str):
        return False
    try:
        datetime.strptime(date_str, '%d-%m-%Y')
        return True
    except ValueError:
        return False


def compare_dates(date1: str, date2: str, comparison: str) -> bool:
    if date1 == "NA" or date2 == "NA":
        return False
    try:
        d1 = datetime.strptime(date1, '%d-%m-%Y')
        d2 = datetime.strptime(date2, '%d-%m-%Y')
        if comparison == 'GT':
            return d1 > d2
        elif comparison == 'LT':
            return d1 < d2
        return False
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Routes – /pet-types
# ---------------------------------------------------------------------------
@app.route('/pet-types', methods=['POST'])
def create_pet_type():
    if not request.is_json:
        return jsonify({"error": "Expected application/json media type"}), 415

    data = request.get_json()
    if not data or 'type' not in data or not isinstance(data['type'], str) or not data['type']:
        return jsonify({"error": "Malformed data"}), 400

    pet_type_name = data['type']

    # Case-insensitive duplicate check
    existing = col.find_one({'type': re.compile(f'^{re.escape(pet_type_name)}$', re.IGNORECASE)})
    if existing:
        return jsonify({"error": "Malformed data"}), 400

    try:
        animal_info = get_animal_info_from_ninja(pet_type_name)
        if animal_info is None:
            return jsonify({"error": "Malformed data"}), 400

        pet_type_id     = get_next_id()
        taxonomy        = animal_info.get('taxonomy', {})
        characteristics = animal_info.get('characteristics', {})
        lifespan        = (parse_lifespan(characteristics['lifespan'])
                           if 'lifespan' in characteristics else None)
        attributes      = parse_attributes(characteristics)

        doc = {
            '_id':        pet_type_id,
            'type':       pet_type_name,
            'family':     taxonomy.get('family', ''),
            'genus':      taxonomy.get('genus', ''),
            'attributes': attributes,
            'lifespan':   lifespan,
            'pets':       []
        }
        col.insert_one(doc)
        return jsonify(format_output(doc)), 201

    except Exception as e:
        error_msg = str(e)
        if "API response code" in error_msg:
            return jsonify({"server error": error_msg}), 500
        return jsonify({"error": "Malformed data"}), 400


@app.route('/pet-types', methods=['GET'])
def get_pet_types():
    valid_fields = {'id', 'type', 'family', 'genus', 'lifespan', 'hasAttribute'}
    results = list(col.find({}))

    for key, value in request.args.items():
        if key not in valid_fields:
            return jsonify([]), 200

        if key == 'id':
            results = [d for d in results if d['_id'] == value]
        elif key == 'lifespan':
            try:
                lifespan_val = int(value)
                results = [d for d in results if d.get('lifespan') == lifespan_val]
            except ValueError:
                results = []
        elif key == 'hasAttribute':
            results = [d for d in results if norm(value) in d.get('attributes', [])]
        else:
            results = [d for d in results if norm(d.get(key, '')) == norm(value)]

    return jsonify([format_output(d) for d in results]), 200


# ---------------------------------------------------------------------------
# Routes – /pet-types/{id}
# ---------------------------------------------------------------------------
@app.route('/pet-types/<id>', methods=['GET'])
def get_pet_type(id):
    doc = col.find_one({'_id': id})
    if not doc:
        return jsonify({"error": "Not found"}), 404
    return jsonify(format_output(doc)), 200


@app.route('/pet-types/<id>', methods=['DELETE'])
def delete_pet_type(id):
    doc = col.find_one({'_id': id})
    if not doc:
        return jsonify({"error": "Not found"}), 404
    if len(doc.get('pets', [])) > 0:
        return jsonify({"error": "Malformed data"}), 400
    col.delete_one({'_id': id})
    return '', 204


# ---------------------------------------------------------------------------
# Routes – /pet-types/{id}/pets
# ---------------------------------------------------------------------------
@app.route('/pet-types/<id>/pets', methods=['POST'])
def create_pet(id):
    doc = col.find_one({'_id': id})
    if not doc:
        return jsonify({"error": "Not found"}), 404

    if not request.is_json:
        return jsonify({"error": "Expected application/json media type"}), 415

    data = request.get_json()
    if not data or 'name' not in data or not isinstance(data['name'], str) or not data['name']:
        return jsonify({"error": "Malformed data"}), 400

    pet_name     = data['name']
    pet_name_key = norm(pet_name)

    # Duplicate name check (case-insensitive)
    if any(p['name_key'] == pet_name_key for p in doc.get('pets', [])):
        return jsonify({"error": "Malformed data"}), 400

    if 'birthdate' not in data:
        birthdate = 'NA'
    else:
        birthdate = data['birthdate']
        if not isinstance(birthdate, str) or not birthdate or not validate_date(birthdate):
            return jsonify({"error": "Malformed data"}), 400

    picture = 'NA'
    if 'picture-url' in data:
        picture_url = data['picture-url']
        if not isinstance(picture_url, str) or not picture_url:
            return jsonify({"error": "Malformed data"}), 400
        try:
            picture = download_image(picture_url, pet_name, doc['type'])
        except Exception:
            return jsonify({"error": "Malformed data"}), 400

    pet_doc = {
        'name':      pet_name,
        'name_key':  pet_name_key,
        'birthdate': birthdate,
        'picture':   picture
    }
    col.update_one({'_id': id}, {'$push': {'pets': pet_doc}})

    return jsonify({'name': pet_name, 'birthdate': birthdate, 'picture': picture}), 201


@app.route('/pet-types/<id>/pets', methods=['GET'])
def get_pets(id):
    doc = col.find_one({'_id': id})
    if not doc:
        return jsonify({"error": "Not found"}), 404

    results = [
        {'name': p['name'], 'birthdate': p['birthdate'], 'picture': p['picture']}
        for p in doc.get('pets', [])
    ]

    gt = request.args.get('birthdateGT', '').strip()
    lt = request.args.get('birthdateLT', '').strip()

    if gt:
        results = [p for p in results if compare_dates(p['birthdate'], gt, 'GT')]
    if lt:
        results = [p for p in results if compare_dates(p['birthdate'], lt, 'LT')]

    return jsonify(results), 200


# ---------------------------------------------------------------------------
# Routes – /pet-types/{id}/pets/{name}
# ---------------------------------------------------------------------------
@app.route('/pet-types/<id>/pets/<n>', methods=['GET'])
def get_pet(id, n):
    doc = col.find_one({'_id': id})
    if not doc:
        return jsonify({"error": "Not found"}), 404

    pet = next((p for p in doc.get('pets', []) if p['name_key'] == norm(n)), None)
    if not pet:
        return jsonify({"error": "Not found"}), 404

    return jsonify({'name': pet['name'], 'birthdate': pet['birthdate'], 'picture': pet['picture']}), 200


@app.route('/pet-types/<id>/pets/<n>', methods=['DELETE'])
def delete_pet(id, n):
    doc = col.find_one({'_id': id})
    if not doc:
        return jsonify({"error": "Not found"}), 404

    pet_name_key = norm(n)
    pet = next((p for p in doc.get('pets', []) if p['name_key'] == pet_name_key), None)
    if not pet:
        return jsonify({"error": "Not found"}), 404

    # Delete local picture file if it exists (pictures are not persistent)
    if pet['picture'] != 'NA':
        picture_path = os.path.join(pictures_dir, pet['picture'])
        if os.path.exists(picture_path):
            os.remove(picture_path)

    col.update_one({'_id': id}, {'$pull': {'pets': {'name_key': pet_name_key}}})
    return '', 204


@app.route('/pet-types/<id>/pets/<n>', methods=['PUT'])
def update_pet(id, n):
    doc = col.find_one({'_id': id})
    if not doc:
        return jsonify({"error": "Not found"}), 404

    pet_name_key = norm(n)
    pet = next((p for p in doc.get('pets', []) if p['name_key'] == pet_name_key), None)
    if not pet:
        return jsonify({"error": "Not found"}), 404

    if not request.is_json:
        return jsonify({"error": "Expected application/json media type"}), 415

    data = request.get_json()
    if not data or 'name' not in data or not isinstance(data['name'], str) or not data['name']:
        return jsonify({"error": "Malformed data"}), 400

    if norm(data['name']) != pet_name_key:
        return jsonify({"error": "Malformed data"}), 400

    if 'birthdate' not in data:
        birthdate = 'NA'
    else:
        birthdate = data['birthdate']
        if not isinstance(birthdate, str) or not birthdate or not validate_date(birthdate):
            return jsonify({"error": "Malformed data"}), 400

    picture = 'NA'
    if 'picture-url' in data:
        picture_url = data['picture-url']
        if not isinstance(picture_url, str) or not picture_url:
            return jsonify({"error": "Malformed data"}), 400
        try:
            if pet['picture'] != 'NA':
                old_path = os.path.join(pictures_dir, pet['picture'])
                if os.path.exists(old_path):
                    os.remove(old_path)
            picture = download_image(picture_url, pet['name'], doc['type'])
        except Exception:
            return jsonify({"error": "Malformed data"}), 400
    else:
        # No new picture supplied – delete the old file (picture becomes NA)
        if pet['picture'] != 'NA':
            old_path = os.path.join(pictures_dir, pet['picture'])
            if os.path.exists(old_path):
                os.remove(old_path)

    updated_pet = {
        'name':      pet['name'],
        'name_key':  pet_name_key,
        'birthdate': birthdate,
        'picture':   picture
    }
    col.update_one(
        {'_id': id, 'pets.name_key': pet_name_key},
        {'$set': {'pets.$': updated_pet}}
    )

    return jsonify({'name': pet['name'], 'birthdate': birthdate, 'picture': picture}), 200


# ---------------------------------------------------------------------------
# Route – /pictures/{file_name}
# ---------------------------------------------------------------------------
@app.route('/pictures/<file_name>', methods=['GET'])
def get_picture(file_name):
    picture_path = os.path.join(pictures_dir, file_name)
    if not os.path.exists(picture_path):
        return jsonify({"error": "Not found"}), 404

    lower = file_name.lower()
    if lower.endswith('.jpg') or lower.endswith('.jpeg'):
        mimetype = 'image/jpeg'
    elif lower.endswith('.png'):
        mimetype = 'image/png'
    else:
        mimetype = 'application/octet-stream'

    return send_file(picture_path, mimetype=mimetype)


# ---------------------------------------------------------------------------
# Route – /kill  (for HA testing: causes the container to crash and restart)
# ---------------------------------------------------------------------------
@app.route('/kill', methods=['GET'])
def kill_container():
    os._exit(1)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)