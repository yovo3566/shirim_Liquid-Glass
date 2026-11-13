import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:just_audio/just_audio.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(
    ChangeNotifierProvider(
      create: (_) => AppState(),
      child: const ShirimApp(),
    ),
  );
}

class SongItem {
  final String id;
  final String title;
  final String artist;
  final String thumb;

  const SongItem({
    required this.id,
    required this.title,
    required this.artist,
    required this.thumb,
  });

  factory SongItem.fromJson(Map<String, dynamic> json) {
    final rawThumb = json['thumb'] ??
        json['thumbnail'] ??
        json['image'] ??
        json['artwork'] ??
        json['img'] ??
        '';

    return SongItem(
      id: (json['id'] ?? '').toString(),
      title: (json['title'] ?? 'Untitled').toString(),
      artist: (json['artist'] ?? json['artists'] ?? 'Unknown artist').toString(),
      thumb: rawThumb.toString(),
    );
  }
}

class AppState extends ChangeNotifier {
  final AudioPlayer _player = AudioPlayer();
  final String baseUrl = _normalizeBaseUrl(
    const String.fromEnvironment(
      'SHIRIM_API_BASE_URL',
      defaultValue: 'http://localhost:8000/',
    ),
  );

  List<SongItem> _playlist = <SongItem>[];
  int _currentIndex = -1;
  bool _isLoading = false;
  List<SongItem> searchResults = <SongItem>[];

  AudioPlayer get player => _player;
  bool get isLoading => _isLoading;

  SongItem? get currentSong =>
      (_currentIndex >= 0 && _currentIndex < _playlist.length)
          ? _playlist[_currentIndex]
          : null;

    static String _normalizeBaseUrl(String value) =>
      value.endsWith('/') ? value : '$value/';

  Future<void> search(String query) async {
    final cleaned = query.trim();
    if (cleaned.isEmpty) {
      searchResults = <SongItem>[];
      notifyListeners();
      return;
    }

    _isLoading = true;
    notifyListeners();

    try {
      final response = await http.get(
        Uri.parse('${baseUrl}api/search?q=${Uri.encodeComponent(cleaned)}'),
      );

      if (response.statusCode == 200) {
        final decoded = json.decode(utf8.decode(response.bodyBytes));
        List<dynamic> payload = <dynamic>[];

        if (decoded is List) {
          payload = decoded;
        } else if (decoded is Map) {
          if (decoded['results'] is List) {
            payload = decoded['results'] as List<dynamic>;
          } else if (decoded['songs'] is List) {
            payload = decoded['songs'] as List<dynamic>;
          }
        }

        searchResults = payload.map((item) {
          if (item is Map<String, dynamic>) {
            return SongItem.fromJson(item);
          }
          if (item is Map) {
            return SongItem.fromJson(Map<String, dynamic>.from(item));
          }
          return const SongItem(
            id: '',
            title: 'Unknown',
            artist: 'Unknown artist',
            thumb: '',
          );
        }).toList();
      } else {
        searchResults = <SongItem>[];
      }
    } catch (error) {
      debugPrint('Search error: $error');
      searchResults = <SongItem>[];
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> playPlaylist(List<SongItem> songs, int initialIndex) async {
    if (songs.isEmpty) {
      return;
    }

    _playlist = songs;
    _currentIndex = initialIndex;
    notifyListeners();

    try {
      final sources = songs
          .map(
            (song) => AudioSource.uri(
              Uri.parse('${baseUrl}stream/${song.id}'),
            ),
          )
          .toList();

      await _player.setAudioSource(
        ConcatenatingAudioSource(children: sources),
        initialIndex: initialIndex,
      );
      await _player.play();
      notifyListeners();
    } catch (error) {
      debugPrint('Playback error: $error');
    }
  }

  Future<void> togglePlayPause() async {
    if (_player.playing) {
      await _player.pause();
    } else {
      await _player.play();
    }
    notifyListeners();
  }

  Future<void> playNext() async {
    await _player.seekToNext();
    notifyListeners();
  }

  Future<void> playPrevious() async {
    await _player.seekToPrevious();
    notifyListeners();
  }

  Future<void> shareSong(SongItem song) async {
    final shareText = 'Listen to ${song.title} by ${song.artist}\n${baseUrl}stream/${song.id}';
    final uri = Uri.parse(
      'mailto:?subject=${Uri.encodeComponent(song.title)}&body=${Uri.encodeComponent(shareText)}',
    );

    if (!await launchUrl(uri)) {
      debugPrint('Could not launch share link for ${song.title}');
    }
  }
}

class ShirimApp extends StatelessWidget {
  const ShirimApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Shirim',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF090909),
        primaryColor: const Color(0xFF7C4DFF),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF7C4DFF),
          secondary: Color(0xFFB388FF),
          surface: Color(0xFF121212),
        ),
        useMaterial3: true,
      ),
      home: const MainScreen(),
    );
  }
}

class MainScreen extends StatefulWidget {
  const MainScreen({super.key});

  @override
  State<MainScreen> createState() => _MainScreenState();
}

class _MainScreenState extends State<MainScreen> {
  final TextEditingController _searchController = TextEditingController();

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Shirim Player'),
        backgroundColor: const Color(0xFF111111),
        foregroundColor: Colors.white,
      ),
      body: Column(
        children: <Widget>[
          Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: TextField(
                    controller: _searchController,
                    keyboardType: TextInputType.text,
                    textInputAction: TextInputAction.search,
                    decoration: InputDecoration(
                      hintText: 'Search for a song or artist',
                      filled: true,
                      fillColor: const Color(0xFF1A1A1A),
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 14,
                      ),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: BorderSide.none,
                      ),
                    ),
                    onSubmitted: (value) => appState.search(value),
                  ),
                ),
                const SizedBox(width: 12),
                IconButton.filled(
                  onPressed: () => appState.search(_searchController.text),
                  icon: const Icon(Icons.search),
                  tooltip: 'Search',
                ),
              ],
            ),
          ),
          if (appState.isLoading)
            const Padding(
              padding: EdgeInsets.all(24),
              child: CircularProgressIndicator(),
            )
          else
            Expanded(
              child: ListView.separated(
                itemCount: appState.searchResults.length,
                separatorBuilder: (_, __) => const Divider(
                  color: Colors.white12,
                  height: 1,
                ),
                itemBuilder: (context, index) {
                  final song = appState.searchResults[index];
                  return ListTile(
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 8,
                    ),
                    leading: ClipRRect(
                      borderRadius: BorderRadius.circular(8),
                      child: song.thumb.isNotEmpty
                          ? Image.network(
                              song.thumb,
                              width: 52,
                              height: 52,
                              fit: BoxFit.cover,
                            )
                          : Container(
                              width: 52,
                              height: 52,
                              color: const Color(0xFF2A2A2A),
                              child: const Icon(Icons.music_note),
                            ),
                    ),
                    title: Text(
                      song.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    subtitle: Text(
                      song.artist,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    trailing: IconButton(
                      icon: const Icon(Icons.share_outlined),
                      onPressed: () => appState.shareSong(song),
                    ),
                    onTap: () => appState.playPlaylist(appState.searchResults, index),
                  );
                },
              ),
            ),
          if (appState.currentSong != null) const MiniPlayer(),
        ],
      ),
    );
  }
}

class MiniPlayer extends StatelessWidget {
  const MiniPlayer({super.key});

  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();
    final song = appState.currentSong;

    if (song == null) {
      return const SizedBox.shrink();
    }

    return Container(
      decoration: const BoxDecoration(
        color: Color(0xFF171717),
        border: Border(
          top: BorderSide(color: Colors.white12),
        ),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: SafeArea(
        top: false,
        child: Row(
          children: <Widget>[
            ClipRRect(
              borderRadius: BorderRadius.circular(10),
              child: song.thumb.isNotEmpty
                  ? Image.network(
                      song.thumb,
                      width: 52,
                      height: 52,
                      fit: BoxFit.cover,
                    )
                  : Container(
                      width: 52,
                      height: 52,
                      color: const Color(0xFF2A2A2A),
                      child: const Icon(Icons.music_note, size: 26),
                    ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(
                    song.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                  Text(
                    song.artist,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: Colors.white70,
                      fontSize: 12,
                    ),
                  ),
                ],
              ),
            ),
            StreamBuilder<PlayerState>(
              stream: appState.player.playerStateStream,
              builder: (context, snapshot) {
                final isPlaying = snapshot.data?.playing ?? false;

                return Row(
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    IconButton(
                      onPressed: () async => appState.playPrevious(),
                      icon: const Icon(Icons.skip_previous),
                    ),
                    IconButton(
                      onPressed: () async => appState.togglePlayPause(),
                      icon: Icon(isPlaying ? Icons.pause : Icons.play_arrow),
                    ),
                    IconButton(
                      onPressed: () async => appState.playNext(),
                      icon: const Icon(Icons.skip_next),
                    ),
                  ],
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}