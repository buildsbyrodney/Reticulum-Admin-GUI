"""Curated, family-friendly, offline response content for the Bot."""
import json
from pathlib import Path

TEST = (
    "No need to test — you made contact! 😄", "Test successful. I can hear you from here.",
    "Yep, you're getting through just fine!", "Contact confirmed. You may stop poking the bot now. 🤖",
    "I heard that. Loud and clear!", "Congratulations, the electrons made it.",
    "Message received. The bot lives!", "Test passed. No smoke detected.",
    "You're in. Welcome to the network.", "Confirmed. Reticulum did the Reticulum thing.",
    "Packet received. Your keyboard works.", "Yep. Still here.",
    "The route held together nicely.", "Signal received with excellent imaginary paperwork.",
    "Contact made. The network survived.", "That test arrived safely at bot headquarters.",
    "All systems acknowledge your existence.", "A tiny packet victory has occurred.",
    "The message landed exactly where it meant to.", "Handshake complete, friendliness enabled.",
    "The bits report a successful journey.", "Connection confirmed. Carry on!",
    "Good news: the path is doing path things.", "Your test has been officially noticed.",
    "No carrier pigeons were required.", "That came through cleanly.",
    "Reticulum says hello back.", "The radio ghosts approve this message.",
    "A wild packet appeared — it was yours.", "Network check: delightfully uneventful.",
    "Your signal reached a very attentive robot.", "Test complete. Nothing caught fire.",
    "Received and cheerfully acknowledged.", "That was a textbook little contact.",
    "The destination was not merely theoretical.", "Your message has successfully crossed the wires.",
    "Status update: still listening.", "Message accepted. Coffee remains optional.",
    "The bot confirms your packet is real.", "The test signal has a clean bill of health.",
)
JOKES = (
    "Why did the packet take a map? It did not want to lose its route.", "I told my network a joke. It replied with a timeout.",
    "Why was the router calm? It knew how to handle traffic.", "The modem and router broke up; they needed more bandwidth.",
    "A packet walks into a bar and asks, ‘Is this the right port?’", "My favorite networking exercise is a good long ping.",
    "Why did the byte go to school? To improve its bit-ucation.", "The antenna felt grounded, so it went outside for better reception.",
    "A packet never tells secrets. It knows they might be broadcast.", "Why did the computer wear glasses? To improve its web sight.",
    "The cable became a comedian because it had great connections.", "Why did the signal cross the road? To reach the other sideband.",
    "A friendly firewall is still hard to get past at parties.", "The network's favorite dance is the packet shuffle.",
    "Why did the server bring an umbrella? It heard there would be cloud cover.", "The radio operator loved concise jokes: less QRM.",
    "A packet asked for directions. The router said, ‘Next hop, then ask again.’", "Why did the computer visit the doctor? It had a bad case of cache cough.",
    "The switch was very decisive. It always knew which way to forward.", "A checksum's favorite game is spot the difference.",
    "Why are radio jokes so reliable? They always have a good carrier.", "The network stayed humble because it knew every route can expire.",
    "A byte went on vacation and came back completely refreshed.", "The engineer named the Wi-Fi ‘Pretty Fly for a Wi-Fi.’ It was a classic.",
    "Why did the packet blush? It saw the open port.",
)
DAD_JOKES = (
    "I only know 25 letters of the alphabet. I don't know why.", "I used to hate facial hair, but then it grew on me.",
    "What do you call a factory that makes okay products? A satisfactory.", "I am reading a book about anti-gravity. It is impossible to put down.",
    "Did you hear about the restaurant on the moon? Great food, no atmosphere.", "Why did the scarecrow win an award? He was outstanding in his field.",
    "I would tell a construction joke, but I am still working on it.", "What do you call cheese that is not yours? Nacho cheese.",
    "I used to be addicted to the hokey pokey, but I turned myself around.", "Why cannot a bicycle stand up by itself? It is two-tired.",
    "What did one wall say to the other wall? I'll meet you at the corner.", "I told a joke about paper. It was tearable.",
    "Why did the coffee file a police report? It got mugged.", "What do you call a bear with no teeth? A gummy bear.",
    "I am friends with all electricians. We have good current connections.", "Why do melons have weddings? Because they cantaloupe.",
    "What do you call a sleeping bull? A bulldozer.", "I wondered why the baseball was getting bigger. Then it hit me.",
    "Why did the math book look sad? It had too many problems.", "What kind of tree fits in your hand? A palm tree.",
    "I have a joke about a roof, but it would go over your head.", "Why did the cookie go to the doctor? It felt crummy.",
    "What do you call a fake noodle? An impasta.", "The shovel was a groundbreaking invention.",
    "I named my dog Five Miles so I can say I walk Five Miles every day.",
)
EIGHT_BALL = (
    "It is decidedly so.", "Signs point to yes.", "Ask again after another announce.", "Reticulum says: probably.",
    "Reply hazy. Try again after coffee.", "Outlook good.", "Better not tell you now.", "My sources say no.",
    "Without a doubt.", "You may rely on it.", "Most likely.", "Concentrate and ask again.", "Don't count on it.",
    "Outlook not so good.", "Very doubtful.", "The route looks promising.", "Ask again after checking the logs.",
    "The packets are undecided.", "All signs point toward a sensible plan.", "Try a smaller question first.",
)
FACTS = (
    "Reticulum can operate over local networks, radio, and TCP.", "An IFAC is shared access protection for an interface.",
    "A TCP Server listens locally; a TCP Client connects outward.", "A path tells Reticulum how to reach a destination.",
    "EME means Earth–Moon–Earth radio communication.", "IPv6 link-local addresses are for a local network segment.",
    "A byte contains eight bits.", "A kilohertz is one thousand cycles per second.", "A megahertz is one million cycles per second.",
    "Ohm's law is V = I × R.", "A resistor limits current.", "A capacitor stores energy in an electric field.",
    "A diode primarily permits current in one direction.", "A transistor can switch or amplify a signal.",
    "DNS translates names into network addresses.", "TCP provides ordered, reliable byte-stream delivery.",
    "UDP does not itself guarantee delivery.", "A router forwards packets between networks.",
    "An antenna converts electrical signals and radio waves.", "Frequency is cycles per second.", "Decibels express a logarithmic ratio.",
    "A checksum helps detect accidental data changes.", "Public-key cryptography uses separate public and private keys.",
    "A network port identifies a service endpoint.", "Mesh networks can route through intermediate nodes.",
)
COFFEE = (
    "Coffee status: critically low. Technician intervention recommended.", "Brew cycle nominal. Operator morale improving.",
    "Unable to locate coffee. Escalating to emergency procedures.", "Coffee detected. Network stability expected to improve.",
    "Preventive maintenance recommendation: refill mug.", "Coffee pressure low. Operator performance may be affected.",
    "Brew cycle complete. System morale restored.", "Caffeine link established.", "The kettle reports a promising uplink.",
    "Diagnostic result: a warm cup would help.", "Coffee cache is empty; fetching locally is advised.",
    "The mug is ready for a fresh configuration.", "No beans were harmed in this status report.",
    "Operator hydration and coffee levels should be reviewed.", "Brew telemetry: aromatic and encouraging.",
    "The machine recommends a coffee-based maintenance window.", "A clean cup is the start of a clean packet trace.",
    "Coffee service is available at the nearest sensible location.", "Caffeine reserve looks adequate for one more troubleshooting session.",
    "The bot cannot brew coffee, but it strongly supports the idea.",
)
RADIO = (
    "QRM detected: someone is talking while I'm trying to drink coffee.", "Fun fact: EME means Earth–Moon–Earth communication.",
    "Your signal report is 5 by 9 in my imagination.", "A good radio contact starts with listening.",
    "Check the coax before blaming the atmosphere.", "Antenna height often matters, but safe installation matters more.",
    "CQ is a general call seeking contacts.", "QSL traditionally confirms a contact.", "A repeater receives and retransmits a signal.",
    "Propagation can change faster than a weather forecast.", "The best filter is often a little patience.",
    "A dummy load is useful when testing without radiating a signal.", "SWR helps describe impedance matching in a feed system.",
    "Grounding and lightning protection deserve careful attention.", "Simple antennas can make excellent contacts.",
    "A clean signal is a neighborly signal.", "Portable operation turns many places into a shack.",
    "The first rule of troubleshooting is to change one thing at a time.", "Radio is equal parts science, craft, and curiosity.",
    "A logbook turns a contact into a remembered contact.",
)

# Canonical KJV entries are generated from midvash/bible-data's 1769 source.
_payload = json.loads((Path(__file__).with_name("kjv.json")).read_text(encoding="utf-8"))
KJV = {reference.lower(): (reference, text) for reference, text in _payload["verses"].items()}
KJV_TOPICS = {
    "faith": ("hebrews 11:1", "hebrews 11:6", "romans 10:9", "ephesians 2:8"),
    "hope": ("jeremiah 17:7", "romans 8:28", "psalm 27:14", "lamentations 3:23"),
    "love": ("john 3:16", "1 corinthians 13:4", "1 john 4:8", "1 john 4:19"),
    "comfort": ("psalm 23:4", "matthew 11:28", "john 14:1", "1 peter 5:7"),
    "strength": ("isaiah 40:31", "isaiah 41:10", "philippians 4:13", "ephesians 6:10"),
    "peace": ("psalm 4:8", "john 14:27", "philippians 4:7", "colossians 3:15"),
    "wisdom": ("proverbs 3:5", "proverbs 3:6", "james 1:5", "proverbs 4:23"),
    "salvation": ("john 3:16", "acts 4:12", "acts 16:31", "romans 6:23"),
    "prayer": ("matthew 7:7", "philippians 4:6", "1 thessalonians 5:17", "james 1:5"),
    "fear": ("psalm 56:3", "isaiah 41:10", "2 timothy 1:7", "joshua 1:9"),
    "guidance": ("psalm 119:105", "proverbs 3:6", "psalm 37:5", "psalm 61:2"),
}
