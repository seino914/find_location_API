import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import googlemaps
from dotenv import load_dotenv
from typing import Dict, List, Set
import math
import logging
import time
from datetime import datetime

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 環境変数の読み込み
load_dotenv()

app = FastAPI(title="店舗情報取得API")

# Google Maps APIクライアントの初期化
gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY"))

class PlaceInfo(BaseModel):
    name: str
    address: str
    place_type: str
    area: str

class LocationRequest(BaseModel):
    prefecture: str
    city: str

class LocationResponse(BaseModel):
    total_restaurants: int
    general_restaurants: List[PlaceInfo]
    izakaya: List[PlaceInfo]
    family_restaurants: List[PlaceInfo]
    ramen_shops: List[PlaceInfo]
    soba_udon_shops: List[PlaceInfo]
    cafes: List[PlaceInfo]
    processing_time: float  # 処理時間を追加

def get_area_grid_points(bounds):
    """
    指定された境界内のグリッドポイントを生成
    """
    ne = bounds['northeast']
    sw = bounds['southwest']
    
    # 約1km間隔でグリッドを作成
    lat_step = 0.009  # 約1km
    lng_step = 0.011  # 約1km
    
    lat_points = math.ceil((ne['lat'] - sw['lat']) / lat_step)
    lng_points = math.ceil((ne['lng'] - sw['lng']) / lng_step)
    
    logger.info(f"グリッドサイズ: {lat_points}x{lng_points} = {lat_points * lng_points}ポイント")
    
    grid_points = []
    for i in range(lat_points):
        for j in range(lng_points):
            lat = sw['lat'] + (i * lat_step)
            lng = sw['lng'] + (j * lng_step)
            if lat <= ne['lat'] and lng <= ne['lng']:
                grid_points.append({'lat': lat, 'lng': lng})
    
    return grid_points

def get_all_places(gmaps, location, keyword, place_type, radius=500):
    """
    特定のキーワードと種類で店舗を検索し、すべての結果を返す
    """
    MAX_RETRIES = 3
    MAX_PAGES = 3  # 最大ページ数を制限
    results = []
    
    try:
        response = gmaps.places_nearby(
            location=location,
            keyword=keyword,
            type=place_type,
            radius=radius,
            language="ja"
        )
        
        # 最初のページの結果を追加
        if 'results' in response:
            results.extend(response['results'])
            logger.debug(f"検索結果: {keyword} - {len(response['results'])}件")
        
        # 次のページがある場合は取得を続ける
        page_count = 1
        while 'next_page_token' in response and page_count < MAX_PAGES:
            page_count += 1
            retry_count = 0
            
            while retry_count < MAX_RETRIES:
                try:
                    time.sleep(2)  # APIの制限に対する待機時間
                    response = gmaps.places_nearby(
                        location=location,
                        page_token=response['next_page_token']
                    )
                    if 'results' in response:
                        results.extend(response['results'])
                        logger.debug(f"追加の検索結果: {keyword} - ページ{page_count} - {len(response['results'])}件")
                    break
                except Exception as e:
                    retry_count += 1
                    if retry_count == MAX_RETRIES:
                        logger.error(f"ページ取得エラー: {keyword} - {str(e)}")
                        break
                    time.sleep(2)
    
    except Exception as e:
        logger.error(f"検索エラー: {keyword} - {str(e)}")
    
    return results

def get_area_name(gmaps, lat, lng):
    """
    座標から地域名を取得
    """
    try:
        result = gmaps.reverse_geocode((lat, lng), language="ja")
        if result:
            # 最も詳細な地域情報を取得
            address_components = result[0]['address_components']
            for component in address_components:
                if 'sublocality' in component['types']:
                    return component['long_name']
            return result[0]['formatted_address'].split(',')[0]
    except Exception as e:
        logger.error(f"地域名取得エラー: ({lat}, {lng}) - {str(e)}")
        return "地域不明"
    return "地域不明"

def convert_to_place_info(places: List[dict], category: str, gmaps) -> List[PlaceInfo]:
    """
    Google Places APIの結果をPlaceInfo形式に変換
    """
    logger.info(f"{category}の変換開始 - {len(places)}件")
    result = []
    for place in places:
        try:
            result.append(PlaceInfo(
                name=place['name'],
                address=place.get('vicinity', '住所不明'),
                place_type=category,
                area=get_area_name(gmaps, place['geometry']['location']['lat'], 
                                 place['geometry']['location']['lng'])
            ))
        except Exception as e:
            logger.error(f"店舗情報変換エラー: {category} - {str(e)}")
    return result

@app.post("/get_place_info", response_model=LocationResponse)
async def get_place_info(location: LocationRequest):
    start_time = time.time()
    logger.info(f"検索開始: {location.prefecture}{location.city}")
    
    try:
        # 住所の組み立て
        address = f"{location.prefecture}{location.city}"
        
        # 位置情報の取得
        geocode_result = gmaps.geocode(address)
        if not geocode_result:
            raise HTTPException(status_code=404, detail="指定された住所が見つかりません")
        
        # 地域の境界を取得
        bounds = geocode_result[0]['geometry']['bounds']
        if not bounds:
            bounds = {
                'northeast': geocode_result[0]['geometry']['location'],
                'southwest': geocode_result[0]['geometry']['location']
            }
        
        # グリッドポイントを生成
        grid_points = get_area_grid_points(bounds)
        logger.info(f"検索ポイント数: {len(grid_points)}")
        
        # 各カテゴリの店舗を格納するリスト
        all_general_restaurants = []
        all_izakaya = []
        all_family_restaurants = []
        all_ramen_shops = []
        all_soba_udon = []
        all_cafes = []
        
        # 各グリッドポイントで検索
        for i, point in enumerate(grid_points, 1):
            logger.info(f"ポイント {i}/{len(grid_points)} の検索中")
            # 各カテゴリの店舗を取得
            all_general_restaurants.extend(get_all_places(gmaps, point, "飲食店", "restaurant"))
            all_izakaya.extend(get_all_places(gmaps, point, "居酒屋", "restaurant"))
            all_family_restaurants.extend(get_all_places(gmaps, point, "ファミリーレストラン", "restaurant"))
            all_ramen_shops.extend(get_all_places(gmaps, point, "ラーメン", "restaurant"))
            all_soba_udon.extend(get_all_places(gmaps, point, "そば OR うどん", "restaurant"))
            all_cafes.extend(get_all_places(gmaps, point, "カフェ OR 喫茶店", "cafe"))
        
        # 重複を排除（place_idで判断）
        logger.info("重複排除処理開始")
        seen_ids = set()
        unique_general_restaurants = []
        unique_izakaya = []
        unique_family_restaurants = []
        unique_ramen_shops = []
        unique_soba_udon = []
        unique_cafes = []
        
        for place in all_general_restaurants:
            if place['place_id'] not in seen_ids:
                seen_ids.add(place['place_id'])
                unique_general_restaurants.append(place)
        
        for place in all_izakaya:
            if place['place_id'] not in seen_ids:
                seen_ids.add(place['place_id'])
                unique_izakaya.append(place)
        
        for place in all_family_restaurants:
            if place['place_id'] not in seen_ids:
                seen_ids.add(place['place_id'])
                unique_family_restaurants.append(place)
        
        for place in all_ramen_shops:
            if place['place_id'] not in seen_ids:
                seen_ids.add(place['place_id'])
                unique_ramen_shops.append(place)
        
        for place in all_soba_udon:
            if place['place_id'] not in seen_ids:
                seen_ids.add(place['place_id'])
                unique_soba_udon.append(place)
        
        for place in all_cafes:
            if place['place_id'] not in seen_ids:
                seen_ids.add(place['place_id'])
                unique_cafes.append(place)
        
        processing_time = time.time() - start_time
        logger.info(f"処理完了: 総処理時間 {processing_time:.2f}秒")
        
        return LocationResponse(
            total_restaurants=len(seen_ids),
            general_restaurants=convert_to_place_info(unique_general_restaurants, "一般飲食店", gmaps),
            izakaya=convert_to_place_info(unique_izakaya, "居酒屋", gmaps),
            family_restaurants=convert_to_place_info(unique_family_restaurants, "ファミリーレストラン", gmaps),
            ramen_shops=convert_to_place_info(unique_ramen_shops, "ラーメン", gmaps),
            soba_udon_shops=convert_to_place_info(unique_soba_udon, "そば・うどん", gmaps),
            cafes=convert_to_place_info(unique_cafes, "カフェ", gmaps),
            processing_time=processing_time
        )
        
    except Exception as e:
        logger.error(f"エラー発生: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
