from backend.config import Config
from backend.services.weather_service import WeatherService

if not Config.OPENWEATHER_API_KEY:
    print("ERROR: OpenWeather API key is not loaded.")
    exit()

service = WeatherService()

lat = 19.10
lon = 72.85

print("\nFetching weather...")
weather = service.get_current_weather(lat, lon)

print("\nWeather Response:")
print("Temperature:", weather.get("temperature_c"), "°C")
print("Feels Like:", weather.get("feels_like_c"), "°C")
print("Humidity:", weather.get("humidity_pct"), "%")
print("Rainfall:", weather.get("precipitation_mm"), "mm")
print("Wind Speed:", weather.get("wind_speed_kmh"), "km/h")
print("Condition:", weather.get("weather_condition"))
print("Description:", weather.get("weather_description"))
print("Source:", weather.get("source"))