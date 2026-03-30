from flask import Flask, request, jsonify
import requests
import os
import random
from pymongo import MongoClient, ReturnDocument
import time

app = Flask(__name__)

MONGO_URL      = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
PETSTORE1_URL  = os.environ.get('PETSTORE1_URL', 'http://pet-store1:8000')
PETSTORE2_URL  = os.environ.get('PETSTORE2_URL', 'http://pet-store2:8000')
OWNER_PC_VALUE = "LovesPetsL2M3n4"

STORES = {1: PETSTORE1_URL, 2: PETSTORE2_URL}


# ---------------------------------------------------------------------------
# MongoDB connection (with retry)
# ---------------------------------------------------------------------------
def connect_to_mongo():
    for attempt in range(30):
        try:
            c = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
            c.admin.command('ping')
            print("[pet-order] Connected to MongoDB", flush=True)
            return c
        except Exception as e:
            print(f"[pet-order] MongoDB attempt {attempt + 1} failed: {e}", flush=True)
            time.sleep(3)
    raise RuntimeError("Could not connect to MongoDB after 30 attempts")


mongo_client    = connect_to_mongo()
db              = mongo_client['petorderdb']
transactions_col = db['transactions']
counters_col    = db['counters']


def get_next_purchase_id() -> int:
    result = counters_col.find_one_and_update(
        {'_id': 'purchase_id'},
        {'$inc': {'value': 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return result['value']


# ---------------------------------------------------------------------------
# Pet-store helpers
# ---------------------------------------------------------------------------
def get_pet_type_and_pets(store_url: str, pet_type_name: str):
    """
    Query a pet-store for the given pet-type name (case-insensitive).
    Returns (pet_type_id, pets_list) or (None, []) if not found.
    """
    try:
        resp = requests.get(
            f"{store_url}/pet-types",
            params={'type': pet_type_name},
            timeout=5
        )
        if resp.status_code != 200:
            return None, []

        pet_types = resp.json()
        # Find exact case-insensitive match
        for pt in pet_types:
            if pt['type'].lower() == pet_type_name.lower():
                # Fetch the pets for this type
                pets_resp = requests.get(
                    f"{store_url}/pet-types/{pt['id']}/pets",
                    timeout=5
                )
                if pets_resp.status_code == 200:
                    return pt['id'], pets_resp.json()
    except Exception as e:
        print(f"[pet-order] Error querying store {store_url}: {e}", flush=True)
    return None, []


def delete_pet_from_store(store_url: str, pet_type_id: str, pet_name: str) -> bool:
    try:
        resp = requests.delete(
            f"{store_url}/pet-types/{pet_type_id}/pets/{pet_name}",
            timeout=5
        )
        return resp.status_code == 204
    except Exception as e:
        print(f"[pet-order] Error deleting pet from store: {e}", flush=True)
        return False


# ---------------------------------------------------------------------------
# POST /purchases
# ---------------------------------------------------------------------------
@app.route('/purchases', methods=['POST'])
def create_purchase():
    if not request.is_json:
        return jsonify({"error": "Expected application/json media type"}), 415

    data = request.get_json()
    if not data:
        return jsonify({"error": "Malformed data"}), 400

    # purchase-id must NOT be supplied by the client
    if 'purchase-id' in data:
        return jsonify({"error": "Malformed data"}), 400

    if ('purchaser' not in data or
            not isinstance(data['purchaser'], str) or
            not data['purchaser']):
        return jsonify({"error": "Malformed data"}), 400

    if ('pet-type' not in data or
            not isinstance(data['pet-type'], str) or
            not data['pet-type']):
        return jsonify({"error": "Malformed data"}), 400

    purchaser     = data['purchaser']
    pet_type_name = data['pet-type']
    store         = data.get('store', None)     # optional: 1 or 2
    pet_name      = data.get('pet-name', None)  # optional, only if store given

    # pet-name can only appear when store is given
    if pet_name is not None and store is None:
        return jsonify({"error": "Malformed data"}), 400

    # Validate store value
    if store is not None and store not in [1, 2]:
        return jsonify({"error": "Malformed data"}), 400

    # -----------------------------------------------------------------------
    # Find and choose a pet
    # -----------------------------------------------------------------------
    chosen_pet       = None
    chosen_store_num = None
    chosen_pt_id     = None
    chosen_store_url = None

    if store is not None and pet_name is not None:
        # Exact store + exact pet name required
        s_url = STORES[store]
        pt_id, pets = get_pet_type_and_pets(s_url, pet_type_name)
        if not pt_id or not pets:
            return jsonify({"error": "No pet of this type is available"}), 400
        found = next(
            (p for p in pets if p['name'].lower() == pet_name.lower()), None
        )
        if not found:
            return jsonify({"error": "No pet of this type is available"}), 400
        chosen_pet, chosen_store_num = found, store
        chosen_pt_id, chosen_store_url = pt_id, s_url

    elif store is not None and pet_name is None:
        # Specific store, random pet of the given type
        s_url = STORES[store]
        pt_id, pets = get_pet_type_and_pets(s_url, pet_type_name)
        if not pt_id or not pets:
            return jsonify({"error": "No pet of this type is available"}), 400
        chosen_pet, chosen_store_num = random.choice(pets), store
        chosen_pt_id, chosen_store_url = pt_id, s_url

    else:
        # No store given – search both stores, pick any available pet at random
        all_options = []
        for s_num, s_url in STORES.items():
            pt_id, pets = get_pet_type_and_pets(s_url, pet_type_name)
            if pt_id and pets:
                for p in pets:
                    all_options.append((p, s_num, pt_id, s_url))

        if not all_options:
            return jsonify({"error": "No pet of this type is available"}), 400

        chosen_pet, chosen_store_num, chosen_pt_id, chosen_store_url = \
            random.choice(all_options)

    # -----------------------------------------------------------------------
    # Delete the chosen pet from its store
    # -----------------------------------------------------------------------
    success = delete_pet_from_store(
        chosen_store_url, chosen_pt_id, chosen_pet['name']
    )
    if not success:
        return jsonify({"error": "No pet of this type is available"}), 400

    # -----------------------------------------------------------------------
    # Assign purchase ID and store transaction in MongoDB
    # -----------------------------------------------------------------------
    purchase_id = get_next_purchase_id()

    transaction_doc = {
        '_id':         purchase_id,          # use purchase_id as Mongo _id
        'purchaser':   purchaser,
        'pet-type':    pet_type_name,
        'store':       chosen_store_num,
        'purchase-id': purchase_id
    }
    transactions_col.insert_one(transaction_doc)

    # Return purchase object (includes pet-name; transaction does not)
    purchase = {
        'purchaser':   purchaser,
        'pet-type':    pet_type_name,
        'store':       chosen_store_num,
        'pet-name':    chosen_pet['name'],
        'purchase-id': purchase_id
    }
    return jsonify(purchase), 201


# ---------------------------------------------------------------------------
# GET /transactions  (owner only)
# ---------------------------------------------------------------------------
@app.route('/transactions', methods=['GET'])
def get_transactions():
    owner_pc = request.headers.get('OwnerPC', '')
    if owner_pc != OWNER_PC_VALUE:
        return jsonify({"error": "Unauthorized"}), 401

    results = list(transactions_col.find({}, {'_id': 0}))

    # Supported query-string fields (field names are case-sensitive)
    valid_fields = {'purchaser', 'pet-type', 'store', 'purchase-id'}

    for key, value in request.args.items():
        if key not in valid_fields:
            # Invalid field → no results (same policy as assignment 1)
            results = []
            break

        if key == 'store':
            try:
                store_val = int(value)
                results = [t for t in results if t.get('store') == store_val]
            except ValueError:
                results = []
        elif key == 'purchase-id':
            try:
                pid_val = int(value)
                results = [t for t in results if t.get('purchase-id') == pid_val]
            except ValueError:
                results = []
        else:
            # Case-insensitive string comparison for purchaser / pet-type
            results = [
                t for t in results
                if str(t.get(key, '')).lower() == value.lower()
            ]

    return jsonify(results), 200


# ---------------------------------------------------------------------------
# GET /kill  (forces the container to crash so Docker Compose restarts it)
# ---------------------------------------------------------------------------
@app.route('/kill', methods=['GET'])
def kill_container():
    os._exit(1)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)