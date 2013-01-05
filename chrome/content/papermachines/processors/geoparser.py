#!/usr/bin/env python2.7
import sys, os, json, logging, traceback, base64, time, codecs, urllib, urllib2
from xml.etree import ElementTree as ET
import textprocessor

class Geoparser(textprocessor.TextProcessor):
	"""
	Geoparsing using Europeana service (experimental)
	"""

	def _basic_params(self):
		self.name = "geoparser"
		self.dry_run = False
		self.require_stopwords = False

	def annotate(self, text):
		values = {'freeText': text[0:10000].encode('utf-8', 'ignore')}
		data = urllib.urlencode(values)
		req = urllib2.Request("http://europeana-geo.isti.cnr.it/geoparser/geoparsing/freeText", data)
		response = urllib2.urlopen(req)
		annotation = response.read()
		return annotation
	
	def get_places(self, xml_string):
		xml_string = xml_string.replace("\n", " ")
		elem = ET.fromstring(xml_string)
		annotated = elem.find('annotatedText')

		current_length = 0
		for entity in annotated.getiterator():
			if entity.tag == 'PLACE':
				place = {"name": entity.text, "entityURI": entity.get("entityURI"), "latitude": entity.get("latitude"), "longitude": entity.get("longitude")}
				if entity.text is not None:
					reference = [current_length, current_length + len(entity.text)]
					current_length += len(entity.text)
					if entity.tail is not None:
						current_length += len(entity.tail)
					yield place, reference
			else:
				if entity.text is not None:
					current_length += len(entity.text)
					if entity.tail is not None:
						current_length += len(entity.tail)

	def run_geoparser(self):
		geo_parsed = {}
		places_by_entityURI = {}

		self.cache_filename = os.path.join(self.out_dir, "geoparser.cache")
		if os.path.exists(self.cache_filename):
			self.cache = json.load(file(self.cache_filename))
		else:
			self.cache = {}

		for filename in self.files:
			logging.info("processing " + filename)
			self.update_progress()

			file_geoparsed = filename.replace(".txt", "_geoparse.json")

			if os.path.exists(file_geoparsed):
				try:
					geoparse_obj = json.load(file(file_geoparsed))
					if "places_by_entityURI" in geoparse_obj:
						continue
					else:
						os.remove(file_geoparsed)
				except:
					logging.error("File " + file_geoparsed + " could not be read.")
					logging.error(traceback.format_exc())

			if not self.dry_run:
				geoparse_obj = {'places_by_entityURI': {}, 'references': {}}
				try:
					id = self.metadata[filename]['itemID']
					str_to_parse = self.metadata[filename]['place']
					last_index = len(str_to_parse)
					str_to_parse += codecs.open(filename, 'r', encoding='utf8').read()[0:(48000 - last_index)] #50k characters, shortened by initial place string

					city = None
					places = set()
					
					xml_filename = filename.replace('.txt', '_geoparse.xml')

					if not os.path.exists(xml_filename):
						annotation = self.annotate(str_to_parse)
						with codecs.open(xml_filename, 'w', encoding='utf8') as xml_file:
							xml_file.write(annotation.decode('utf-8'))
					else:
						with codecs.open(xml_filename, 'r', encoding='utf8') as xml_file:
							annotation = xml_file.read()

					for place, reference in self.get_places(annotation):
						entityURI = place["entityURI"]
						geoparse_obj['places_by_entityURI'][entityURI] = {'name': place["name"], 'type': 'unknown', 'coordinates': [place["longitude"], place["latitude"]]}

						if reference[0] < last_index:
							city = entityURI
						else:
							places.add(entityURI)
							if not entityURI in geoparse_obj['references']:
								geoparse_obj['references'][entityURI] = []
							geoparse_obj['references'][entityURI].append((reference[0] - last_index, reference[1] - last_index))

					if city is None and self.metadata[filename]['place'] != "":
						try:
							query_str = self.metadata[filename]['place']
							if query_str in self.cache:
								result = self.cache.get(query_str)
								if result is not None:
									geoparse_obj['places_by_entityURI'][result["entityURI"]] = {'name': result["name"], 'type': result["fcodeName"], 'coordinates': [result["lng"], result["lat"]]}
									places.add(result["entityURI"])
									city = result["entityURI"]
							else:
								search_for = {"q": query_str}
								query_url = "http://ws.geonames.org/searchJSON?%s" % urllib.urlencode(search_for)
								result_obj = json.load(urllib2.urlopen(query_url))
								result_places = result_obj.get("geonames", [])
								if len(result_places) > 0:
									result_place = result_places[0]
									self.cache[query_str] = result_place
									self.cache[query_str].update({"entityURI": "http://sws.geonames.org/" + str(result_place.get("geonameId")) })
									result = self.cache[query_str]
									geoparse_obj['places_by_entityURI'][result["entityURI"]] = {'name': result["name"], 'type': result["fcodeName"], 'coordinates': [result["lng"], result["lat"]]}
									places.add(result["entityURI"])
									city = result["entityURI"]
								else:
									self.cache[query_str] = None
								json.dump(self.cache, file(self.cache_filename, 'w'))
						except:
							logging.error("No city found for %s" % id)
							logging.error(traceback.format_exc())

					geoparse_obj['places'] = list(places)
					geoparse_obj['city'] = city
					json.dump(geoparse_obj, file(file_geoparsed, 'w'))
					time.sleep(0.2)
				except (KeyboardInterrupt, SystemExit):
					raise
				except:
					logging.error(traceback.format_exc())

			geo_parsed[filename] = geoparse_obj.get('places', [])
			self.metadata[filename]['city'] = geoparse_obj.get('city')
			for entityURI, data in geoparse_obj.get('places_by_entityURI', {}).iteritems():
				places_by_entityURI[entityURI] = data

		places = {}
		for filename, entityURIs in geo_parsed.iteritems():
			year = self.metadata[filename]["year"]
			for entityURI in entityURIs:
				if entityURI in places_by_entityURI:
					if entityURI not in places:
						places[entityURI] = {}
						places[entityURI]["name"] = places_by_entityURI[entityURI]["name"]
						places[entityURI]["type"] = places_by_entityURI[entityURI]["type"]
						places[entityURI]["coordinates"] = places_by_entityURI[entityURI]["coordinates"]
						places[entityURI]["weight"] = {year: 1}
					else:
						if year not in places[entityURI]["weight"]:
							places[entityURI]["weight"][year] = 1
						else:
							places[entityURI]["weight"][year] += 1
		self.geo_parsed = geo_parsed
		self.places = places
		self.places_by_entityURI = places_by_entityURI